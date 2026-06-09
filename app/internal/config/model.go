package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"smartclassdownloader/internal/platform"
)

type Settings struct {
	DownloadDir            string `json:"download_dir"`
	AutoLogin              bool   `json:"auto_login"`
	AutoWhisper            bool   `json:"auto_whisper"`
	ASREngine              string `json:"asr_engine"`
	WhisperURL             string `json:"whisper_url"`
	MaxDownloadConcurrent  int    `json:"max_download_concurrent"`
	MaxChunkWorkers        int    `json:"max_chunk_workers"`
	EnableResume           bool   `json:"enable_resume"`
	Aria2Path              string `json:"aria2_path"`
	Aria2AutoDownload      bool   `json:"aria2_auto_download"`
	NetworkTimeoutSeconds  int    `json:"network_timeout"`
	DownloadTimeoutSeconds int    `json:"download_timeout"`
	MaxRetries             int    `json:"max_retries"`
	RetryDelaySeconds      int    `json:"retry_delay"`
	DefaultVGA             bool   `json:"default_vga"`
	DefaultVideo1          bool   `json:"default_video1"`
	DefaultVideo2          bool   `json:"default_video2"`
	DefaultPPT             bool   `json:"default_ppt"`
	DefaultWhisperVGA      bool   `json:"default_whisper_vga"`
	DefaultWhisperVideo1   bool   `json:"default_whisper_video1"`
	DefaultWhisperVideo2   bool   `json:"default_whisper_video2"`
}

func DefaultSettings(layout platform.Layout) Settings {
	return Settings{
		DownloadDir: layout.DownloadsDir, AutoLogin: true, ASREngine: "funasr",
		WhisperURL: "http://127.0.0.1:8001/", MaxDownloadConcurrent: 3,
		MaxChunkWorkers: 8, EnableResume: true, Aria2AutoDownload: true,
		NetworkTimeoutSeconds: 30, DownloadTimeoutSeconds: 120, MaxRetries: 2,
		RetryDelaySeconds: 5, DefaultVGA: true, DefaultVideo1: true,
	}
}

func Normalize(settings Settings, layout platform.Layout) Settings {
	if strings.TrimSpace(settings.DownloadDir) == "" {
		settings.DownloadDir = layout.DownloadsDir
	}
	if settings.ASREngine == "" {
		settings.ASREngine = "funasr"
	}
	if settings.WhisperURL == "" {
		settings.WhisperURL = "http://127.0.0.1:8001/"
	}
	if settings.ASREngine == "funasr" && strings.Contains(settings.WhisperURL, ":8000") {
		settings.WhisperURL = strings.Replace(settings.WhisperURL, ":8000", ":8001", 1)
	}
	if settings.ASREngine == "whisper" && strings.Contains(settings.WhisperURL, ":8001") {
		settings.WhisperURL = strings.Replace(settings.WhisperURL, ":8001", ":8000", 1)
	}
	return settings
}

func (settings Settings) Validate() error {
	if settings.MaxDownloadConcurrent < 1 || settings.MaxDownloadConcurrent > 10 {
		return fmt.Errorf("下载并发数必须在 1-10 之间")
	}
	if settings.MaxChunkWorkers < 1 || settings.MaxChunkWorkers > 32 {
		return fmt.Errorf("分块下载线程数必须在 1-32 之间")
	}
	if settings.NetworkTimeoutSeconds < 10 || settings.NetworkTimeoutSeconds > 300 {
		return fmt.Errorf("网络超时时间必须在 10-300 秒之间")
	}
	if settings.DownloadTimeoutSeconds < 30 || settings.DownloadTimeoutSeconds > 600 {
		return fmt.Errorf("下载超时时间必须在 30-600 秒之间")
	}
	if settings.MaxRetries < 1 || settings.MaxRetries > 5 {
		return fmt.Errorf("最大重试次数必须在 1-5 之间")
	}
	if settings.RetryDelaySeconds < 1 || settings.RetryDelaySeconds > 30 {
		return fmt.Errorf("重试延迟必须在 1-30 秒之间")
	}
	if settings.ASREngine != "whisper" && settings.ASREngine != "funasr" {
		return fmt.Errorf("不支持的语音识别引擎: %s", settings.ASREngine)
	}
	if settings.Aria2Path != "" {
		if info, err := os.Stat(settings.Aria2Path); err != nil || info.IsDir() {
			return fmt.Errorf("aria2c 路径不存在: %s", settings.Aria2Path)
		}
	}
	parent := filepath.Dir(settings.DownloadDir)
	if _, err := os.Stat(parent); err != nil {
		return fmt.Errorf("下载目录的父目录不存在: %s", parent)
	}
	return nil
}

func (settings Settings) ASRServiceURL() string {
	return settings.WhisperURL
}
