package platform

import (
	"fmt"
	"net"
)

func FindAvailablePort(host string, preferred int) (int, error) {
	addr := fmt.Sprintf("%s:%d", host, preferred)
	listener, err := net.Listen("tcp", addr)
	if err == nil {
		defer listener.Close()
		return preferred, nil
	}
	listener, err = net.Listen("tcp", net.JoinHostPort(host, "0"))
	if err != nil {
		return 0, err
	}
	defer listener.Close()
	return listener.Addr().(*net.TCPAddr).Port, nil
}

func IsPortFree(host string, port int) bool {
	listener, err := net.Listen("tcp", fmt.Sprintf("%s:%d", host, port))
	if err != nil {
		return false
	}
	_ = listener.Close()
	return true
}
