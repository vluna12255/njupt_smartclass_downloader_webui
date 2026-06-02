package media

import (
	"encoding/binary"
	"os"
	"path/filepath"
	"testing"
)

func TestValidateWAVAcceptsCompletePCMFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "audio.wav")
	if err := os.WriteFile(path, pcmWAV([]byte{1, 2, 3, 4}), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := ValidateWAV(path); err != nil {
		t.Fatalf("ValidateWAV() error = %v", err)
	}
}

func TestValidateWAVRejectsTruncatedData(t *testing.T) {
	path := filepath.Join(t.TempDir(), "audio.wav")
	body := pcmWAV([]byte{1, 2, 3, 4})
	body = body[:len(body)-2]
	if err := os.WriteFile(path, body, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := ValidateWAV(path); err == nil {
		t.Fatal("ValidateWAV() accepted truncated WAV file")
	}
}

func pcmWAV(data []byte) []byte {
	body := make([]byte, 44+len(data))
	copy(body[0:], "RIFF")
	binary.LittleEndian.PutUint32(body[4:], uint32(len(body)-8))
	copy(body[8:], "WAVE")
	copy(body[12:], "fmt ")
	binary.LittleEndian.PutUint32(body[16:], 16)
	binary.LittleEndian.PutUint16(body[20:], 1)
	binary.LittleEndian.PutUint16(body[22:], 1)
	binary.LittleEndian.PutUint32(body[24:], 16000)
	binary.LittleEndian.PutUint32(body[28:], 32000)
	binary.LittleEndian.PutUint16(body[32:], 2)
	binary.LittleEndian.PutUint16(body[34:], 16)
	copy(body[36:], "data")
	binary.LittleEndian.PutUint32(body[40:], uint32(len(data)))
	copy(body[44:], data)
	return body
}
