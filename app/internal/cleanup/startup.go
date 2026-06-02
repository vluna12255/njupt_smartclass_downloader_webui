package cleanup

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"smartclassdownloader/internal/config"
	"smartclassdownloader/internal/platform"
)

type Service struct {
	layout platform.Layout
	config interface{ Current() config.Settings }
}

type Summary struct {
	LogsRemoved      int
	TemporaryRemoved int
	LogsError        error
	TemporaryError   error
}

func NewService(layout platform.Layout, provider interface{ Current() config.Settings }) *Service {
	return &Service{layout: layout, config: provider}
}

func (service *Service) CleanLogs() (int, error) {
	return CleanLogs(service.layout.LogsDir)
}

func CleanLogs(root string) (int, error) {
	entries, err := os.ReadDir(root)
	if os.IsNotExist(err) {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	count := 0
	var removeErrors []error
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		path := filepath.Join(root, entry.Name())
		if err := os.Remove(path); err != nil {
			removeErrors = append(removeErrors, fmt.Errorf("remove %s: %w", entry.Name(), err))
			continue
		}
		count++
	}
	return count, errors.Join(removeErrors...)
}

func (service *Service) CleanTemporaryDownloads() (int, error) {
	return removeMatching(service.config.Current().DownloadDir, func(path string, info fs.FileInfo) bool {
		name := strings.ToLower(info.Name())
		return !info.IsDir() && (strings.Contains(name, ".tmp") || strings.HasPrefix(name, "audio_") && strings.HasSuffix(name, ".wav"))
	})
}

func removeMatching(root string, match func(string, fs.FileInfo) bool) (int, error) {
	count := 0
	var removeErrors []error
	err := filepath.Walk(root, func(path string, info fs.FileInfo, err error) error {
		if err != nil {
			removeErrors = append(removeErrors, err)
			return nil
		}
		if path != root && match(path, info) {
			if removeErr := os.Remove(path); removeErr != nil {
				removeErrors = append(removeErrors, fmt.Errorf("remove %s: %w", path, removeErr))
			} else {
				count++
			}
		}
		return nil
	})
	if os.IsNotExist(err) {
		return count, nil
	}
	return count, errors.Join(err, errors.Join(removeErrors...))
}
