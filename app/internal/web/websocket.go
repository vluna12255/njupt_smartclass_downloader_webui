package web

import (
	"bufio"
	"context"
	"crypto/sha1"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"
	"sync"

	"smartclassdownloader/internal/task"
)

type wsClient struct {
	connection net.Conn
	reader     *bufio.Reader
	send       chan []byte
	closeOnce  sync.Once
}

type Hub struct {
	mu      sync.RWMutex
	clients map[*wsClient]struct{}
	events  *task.EventBus
}

func NewHub(events *task.EventBus) *Hub {
	return &Hub{clients: map[*wsClient]struct{}{}, events: events}
}

func (hub *Hub) Run(ctx context.Context) {
	events, unsubscribe := hub.events.Subscribe(128)
	defer unsubscribe()
	for {
		select {
		case <-ctx.Done():
			hub.Close()
			return
		case event, ok := <-events:
			if !ok {
				hub.Close()
				return
			}
			body, _ := json.Marshal(event)
			hub.broadcast(body)
		}
	}
}

func (hub *Hub) Count() int {
	hub.mu.RLock()
	defer hub.mu.RUnlock()
	return len(hub.clients)
}

func (hub *Hub) add(client *wsClient) {
	hub.mu.Lock()
	hub.clients[client] = struct{}{}
	hub.mu.Unlock()
}

func (hub *Hub) remove(client *wsClient) {
	hub.mu.Lock()
	if _, ok := hub.clients[client]; ok {
		delete(hub.clients, client)
		client.close()
	}
	hub.mu.Unlock()
}

func (hub *Hub) Close() {
	hub.mu.Lock()
	for client := range hub.clients {
		client.close()
	}
	hub.clients = map[*wsClient]struct{}{}
	hub.mu.Unlock()
}

func (hub *Hub) broadcast(body []byte) {
	hub.mu.RLock()
	defer hub.mu.RUnlock()
	for client := range hub.clients {
		if !client.queue(body) {
			go hub.remove(client)
		}
	}
}

func upgradeWebSocket(w http.ResponseWriter, request *http.Request) (*wsClient, error) {
	if !strings.EqualFold(request.Header.Get("Upgrade"), "websocket") {
		return nil, fmt.Errorf("websocket upgrade required")
	}
	hijacker, ok := w.(http.Hijacker)
	if !ok {
		return nil, fmt.Errorf("response writer cannot hijack")
	}
	connection, buffer, err := hijacker.Hijack()
	if err != nil {
		return nil, err
	}
	key := request.Header.Get("Sec-WebSocket-Key")
	digest := sha1.Sum([]byte(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"))
	accept := base64.StdEncoding.EncodeToString(digest[:])
	_, err = buffer.WriteString("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + "\r\n\r\n")
	if err == nil {
		err = buffer.Flush()
	}
	if err != nil {
		_ = connection.Close()
		return nil, err
	}
	return &wsClient{connection: connection, reader: buffer.Reader, send: make(chan []byte, 32)}, nil
}

func (client *wsClient) close() {
	client.closeOnce.Do(func() {
		close(client.send)
		_ = client.connection.Close()
	})
}

func (client *wsClient) queue(body []byte) (ok bool) {
	defer func() {
		if recover() != nil {
			ok = false
		}
	}()
	select {
	case client.send <- body:
		return true
	default:
		return false
	}
}

func (client *wsClient) writeLoop() {
	for body := range client.send {
		if err := writeFrame(client.connection, 1, body); err != nil {
			client.close()
			return
		}
	}
}

func (client *wsClient) readLoop(onText func(string)) error {
	for {
		opcode, body, err := readFrame(client.reader)
		if err != nil {
			return err
		}
		switch opcode {
		case 1:
			onText(string(body))
		case 8:
			return io.EOF
		case 9:
			if err := writeFrame(client.connection, 10, body); err != nil {
				return err
			}
		}
	}
}

func readFrame(reader *bufio.Reader) (byte, []byte, error) {
	header := make([]byte, 2)
	if _, err := io.ReadFull(reader, header); err != nil {
		return 0, nil, err
	}
	opcode := header[0] & 0x0f
	masked := header[1]&0x80 != 0
	length := uint64(header[1] & 0x7f)
	if length == 126 {
		var size uint16
		if err := binary.Read(reader, binary.BigEndian, &size); err != nil {
			return 0, nil, err
		}
		length = uint64(size)
	} else if length == 127 {
		if err := binary.Read(reader, binary.BigEndian, &length); err != nil {
			return 0, nil, err
		}
	}
	var mask []byte
	if masked {
		mask = make([]byte, 4)
		if _, err := io.ReadFull(reader, mask); err != nil {
			return 0, nil, err
		}
	}
	body := make([]byte, int(length))
	if _, err := io.ReadFull(reader, body); err != nil {
		return 0, nil, err
	}
	if masked {
		for index := range body {
			body[index] ^= mask[index%4]
		}
	}
	return opcode, body, nil
}

func writeFrame(writer io.Writer, opcode byte, body []byte) error {
	header := []byte{0x80 | opcode}
	switch length := len(body); {
	case length < 126:
		header = append(header, byte(length))
	case length <= 65535:
		header = append(header, 126, byte(length>>8), byte(length))
	default:
		header = append(header, 127)
		size := make([]byte, 8)
		binary.BigEndian.PutUint64(size, uint64(length))
		header = append(header, size...)
	}
	if _, err := writer.Write(header); err != nil {
		return err
	}
	_, err := writer.Write(body)
	return err
}
