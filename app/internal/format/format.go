package format

import (
	"fmt"
	"math"
)

func Size(bytes float64, decimals int) string {
	units := []string{"B", "KB", "MB", "GB", "TB", "PB"}
	value := bytes
	for _, unit := range units {
		if value < 1024 || unit == units[len(units)-1] {
			if unit == "B" && decimals <= 1 {
				return fmt.Sprintf("%d B", int64(value))
			}
			return fmt.Sprintf("%.*f %s", decimals, value, unit)
		}
		value /= 1024
	}
	return fmt.Sprintf("%.*f PB", decimals, value)
}

func Duration(seconds int64) string {
	if seconds < 60 {
		return fmt.Sprintf("%ds", seconds)
	}
	if seconds < 3600 {
		return fmt.Sprintf("%dm %ds", seconds/60, seconds%60)
	}
	return fmt.Sprintf("%dh %dm", seconds/3600, (seconds%3600)/60)
}

func Speed(bytesPerSecond float64) string {
	if bytesPerSecond < 0.1 {
		return "0 KB/s"
	}
	if bytesPerSecond < 1024*1024 {
		return fmt.Sprintf("%.0f KB/s", bytesPerSecond/1024)
	}
	return fmt.Sprintf("%.2f MB/s", bytesPerSecond/1024/1024)
}

func ETA(total, downloaded int64, speed float64) string {
	if total <= 0 || speed < 1024 {
		return "--"
	}
	remaining := total - downloaded
	if remaining <= 0 {
		return "0s"
	}
	return Duration(int64(math.Floor(float64(remaining) / speed)))
}
