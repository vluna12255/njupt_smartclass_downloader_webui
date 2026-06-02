package platform

import "fmt"

const ReservedDiskBytes uint64 = 1024 * 1024 * 1024

func CheckSpace(path string, required, reserved uint64) error {
	available, err := AvailableBytes(path)
	if err != nil {
		return err
	}
	if reserved == 0 {
		reserved = ReservedDiskBytes
	}
	if available < required+reserved {
		return fmt.Errorf("磁盘空间不足: 需要 %d 字节并预留 %d 字节，可用 %d 字节", required, reserved, available)
	}
	return nil
}
