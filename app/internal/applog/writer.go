package applog

import (
	"fmt"
	"os"
	"path/filepath"
	"sync"
)

type rotatingFileWriter struct {
	mu       sync.Mutex
	path     string
	maxBytes int64
	backups  int
	file     *os.File
	size     int64
}

func newRotatingFileWriter(path string, maxBytes int64, backups int) (*rotatingFileWriter, error) {
	if maxBytes < 0 {
		return nil, fmt.Errorf("log max bytes must not be negative")
	}
	if backups < 0 {
		return nil, fmt.Errorf("log backup count must not be negative")
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, fmt.Errorf("create log directory: %w", err)
	}
	writer := &rotatingFileWriter{path: path, maxBytes: maxBytes, backups: backups}
	if err := writer.open(); err != nil {
		return nil, err
	}
	return writer, nil
}

func (writer *rotatingFileWriter) Write(body []byte) (int, error) {
	writer.mu.Lock()
	defer writer.mu.Unlock()
	if writer.file == nil {
		return 0, fmt.Errorf("log file is closed")
	}
	if writer.maxBytes > 0 && writer.size > 0 && writer.size+int64(len(body)) > writer.maxBytes {
		if err := writer.rotate(); err != nil {
			return 0, err
		}
	}
	written, err := writer.file.Write(body)
	writer.size += int64(written)
	return written, err
}

func (writer *rotatingFileWriter) Close() error {
	writer.mu.Lock()
	defer writer.mu.Unlock()
	if writer.file == nil {
		return nil
	}
	err := writer.file.Close()
	writer.file = nil
	return err
}

func (writer *rotatingFileWriter) rotate() error {
	if err := writer.file.Close(); err != nil {
		return err
	}
	writer.file = nil
	if writer.backups > 0 {
		for index := writer.backups - 1; index >= 1; index-- {
			if err := replaceFile(backupPath(writer.path, index), backupPath(writer.path, index+1)); err != nil {
				return err
			}
		}
		if err := replaceFile(writer.path, backupPath(writer.path, 1)); err != nil {
			return err
		}
	} else if err := os.Remove(writer.path); err != nil && !os.IsNotExist(err) {
		return err
	}
	return writer.open()
}

func (writer *rotatingFileWriter) open() error {
	file, err := os.OpenFile(writer.path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return fmt.Errorf("open log file: %w", err)
	}
	info, err := file.Stat()
	if err != nil {
		_ = file.Close()
		return fmt.Errorf("stat log file: %w", err)
	}
	writer.file = file
	writer.size = info.Size()
	return nil
}

func backupPath(path string, index int) string {
	return fmt.Sprintf("%s.%d", path, index)
}

func replaceFile(source, target string) error {
	if err := os.Remove(target); err != nil && !os.IsNotExist(err) {
		return err
	}
	if err := os.Rename(source, target); err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}
