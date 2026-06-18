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
	TemporaryRemoved int
	TemporaryError   error
}

func NewService(layout platform.Layout, provider interface{ Current() config.Settings }) *Service {
	return &Service{layout: layout, config: provider}
}

func (service *Service) CleanTemporaryDownloads() (int, error) {
	root := service.config.Current().DownloadDir
	temporaryRemoved, temporaryErr := removeMatching(root, func(path string, info fs.FileInfo) bool {
		name := strings.ToLower(info.Name())
		return !info.IsDir() && (strings.Contains(name, ".tmp") || strings.HasPrefix(name, "audio_") && strings.HasSuffix(name, ".wav"))
	})
	aria2Removed, aria2Err := removeAria2Residues(root)
	return temporaryRemoved + aria2Removed, errors.Join(temporaryErr, aria2Err)
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

func removeAria2Residues(root string) (int, error) {
	count := 0
	var removeErrors []error
	err := filepath.Walk(root, func(path string, info fs.FileInfo, err error) error {
		if err != nil {
			removeErrors = append(removeErrors, err)
			return nil
		}
		if info.IsDir() || !isManagedAria2Control(info.Name()) {
			return nil
		}
		target := path[:len(path)-len(".aria2")]
		if targetInfo, statErr := os.Stat(target); statErr == nil {
			if targetInfo.IsDir() {
				return nil
			}
			if removeErr := os.Remove(target); removeErr != nil {
				removeErrors = append(removeErrors, fmt.Errorf("remove %s: %w", target, removeErr))
				return nil
			}
			count++
		} else if !os.IsNotExist(statErr) {
			removeErrors = append(removeErrors, fmt.Errorf("stat %s: %w", target, statErr))
			return nil
		}
		if removeErr := os.Remove(path); removeErr != nil {
			removeErrors = append(removeErrors, fmt.Errorf("remove %s: %w", path, removeErr))
		} else {
			count++
		}
		return nil
	})
	if os.IsNotExist(err) {
		return count, errors.Join(removeErrors...)
	}
	return count, errors.Join(err, errors.Join(removeErrors...))
}

func isManagedAria2Control(name string) bool {
	lower := strings.ToLower(name)
	if !strings.HasSuffix(lower, ".aria2") {
		return false
	}
	target := strings.TrimSuffix(lower, ".aria2")
	return isManagedVideoName(target)
}

func isManagedVideoName(name string) bool {
	for _, prefix := range []string{"video1", "video2", "vga"} {
		if name == prefix+".mp4" {
			return true
		}
		if strings.HasPrefix(name, prefix+".part") && strings.HasSuffix(name, ".mp4") {
			return true
		}
	}
	return false
}
