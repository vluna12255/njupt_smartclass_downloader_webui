package media

import (
	"context"
	"encoding/binary"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"

	"smartclassdownloader/internal/platform"
)

type FFmpeg struct {
	binary string
}

func NewFFmpeg(layout platform.Layout) *FFmpeg {
	name := "ffmpeg"
	if runtime.GOOS == "windows" {
		name = "ffmpeg.exe"
	}
	return &FFmpeg{binary: filepath.Join(layout.BinDir, name)}
}

func (ffmpeg *FFmpeg) Exists() bool {
	info, err := os.Stat(ffmpeg.binary)
	return err == nil && !info.IsDir()
}

func (ffmpeg *FFmpeg) ConvertToWAV(ctx context.Context, input, output string) error {
	if !ffmpeg.Exists() {
		return fmt.Errorf("找不到 FFmpeg: %s", ffmpeg.binary)
	}
	if info, err := os.Stat(input); err != nil || !info.Mode().IsRegular() || info.Size() == 0 {
		return fmt.Errorf("待转换视频不存在或为空: %s", input)
	}
	if err := os.MkdirAll(filepath.Dir(output), 0o755); err != nil {
		return err
	}
	temporary := output + ".tmp.wav"
	_ = os.Remove(temporary)
	defer os.Remove(temporary)
	process, err := platform.StartManagedProcess(ctx, platform.CommandSpec{
		Path: ffmpeg.binary, Hidden: true, Env: platform.BaseEnvironment(nil),
		Args: []string{"-y", "-i", input, "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-af", "highpass=f=200, lowpass=f=3000, dynaudnorm=p=0.9", temporary},
	})
	if err != nil {
		return err
	}
	if err := process.Wait(); err != nil {
		return fmt.Errorf("FFmpeg 音频转换失败: %w", err)
	}
	if err := ValidateWAV(temporary); err != nil {
		return fmt.Errorf("FFmpeg 音频转换结果无效: %w", err)
	}
	if err := os.Remove(output); err != nil && !os.IsNotExist(err) {
		return err
	}
	if err := os.Rename(temporary, output); err != nil {
		return fmt.Errorf("保存 WAV 文件失败: %w", err)
	}
	return ValidateWAV(output)
}

func ValidateWAV(path string) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || info.Size() < 44 {
		return fmt.Errorf("WAV 文件过小")
	}
	header := make([]byte, 12)
	if _, err := io.ReadFull(file, header); err != nil {
		return err
	}
	if string(header[:4]) != "RIFF" || string(header[8:12]) != "WAVE" {
		return fmt.Errorf("缺少 RIFF/WAVE 文件头")
	}
	var hasFormat bool
	for {
		chunk := make([]byte, 8)
		if _, err := io.ReadFull(file, chunk); err != nil {
			if err == io.EOF || err == io.ErrUnexpectedEOF {
				break
			}
			return err
		}
		size := int64(binary.LittleEndian.Uint32(chunk[4:]))
		switch string(chunk[:4]) {
		case "fmt ":
			hasFormat = size >= 16
		case "data":
			if !hasFormat {
				return fmt.Errorf("缺少 WAV 格式块")
			}
			if size <= 0 {
				return fmt.Errorf("WAV 音频数据为空")
			}
			current, _ := file.Seek(0, io.SeekCurrent)
			if current+size > info.Size() {
				return fmt.Errorf("WAV 音频数据未写完整")
			}
			return nil
		}
		skip := size
		if skip%2 != 0 {
			skip++
		}
		if _, err := file.Seek(skip, io.SeekCurrent); err != nil {
			return err
		}
	}
	return fmt.Errorf("缺少 WAV 音频数据块")
}
