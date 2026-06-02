//go:build !windows

package platform

func IsVCRedistInstalled() (bool, error) {
	return true, nil
}
