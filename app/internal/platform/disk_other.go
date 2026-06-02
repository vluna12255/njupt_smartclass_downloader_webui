//go:build !windows

package platform

import (
	"path/filepath"
	"syscall"
)

func AvailableBytes(path string) (uint64, error) {
	var stat syscall.Statfs_t
	if err := syscall.Statfs(filepath.Dir(path), &stat); err != nil {
		return 0, err
	}
	return stat.Bavail * uint64(stat.Bsize), nil
}
