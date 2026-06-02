package aria2

import (
	"archive/zip"
	"context"
	"crypto/sha256"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"

	"smartclassdownloader/internal/config"
	"smartclassdownloader/internal/platform"
)

const (
	aria2Version       = "1.37.0"
	aria2WindowsURL    = "https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip"
	aria2WindowsSHA256 = "67d015301eef0b612191212d564c5bb0a14b5b9c4796b76454276a4d28d9b288"
)

type BinaryManager struct {
	layout platform.Layout
	config interface{ Current() config.Settings }
}

func NewBinaryManager(layout platform.Layout, provider interface{ Current() config.Settings }) *BinaryManager {
	return &BinaryManager{layout: layout, config: provider}
}

func (manager *BinaryManager) Ensure(ctx context.Context) (string, error) {
	settings := manager.config.Current()
	candidates := []string{settings.Aria2Path, os.Getenv("ARIA2C_PATH"), filepath.Join(manager.layout.BinDir, executableName("aria2c"))}
	if path, err := exec.LookPath("aria2c"); err == nil {
		candidates = append(candidates, path)
	}
	for _, candidate := range candidates {
		if candidate == "" {
			continue
		}
		if info, err := os.Stat(candidate); err == nil && !info.IsDir() {
			path, err := filepath.Abs(candidate)
			if err == nil {
				logger.Debugf("using aria2c binary=%s", path)
			}
			return path, err
		}
	}
	if runtime.GOOS != "windows" {
		return "", fmt.Errorf("未找到 aria2c，请先安装 aria2")
	}
	if !settings.Aria2AutoDownload {
		return "", fmt.Errorf("未找到 aria2c，且设置中已关闭 aria2 自动下载")
	}
	logger.Infof("aria2c not found; downloading managed aria2 version=%s", aria2Version)
	return manager.downloadWindowsBinary(ctx)
}

func (manager *BinaryManager) downloadWindowsBinary(ctx context.Context) (string, error) {
	if err := os.MkdirAll(manager.layout.BinDir, 0o755); err != nil {
		return "", err
	}
	archive := filepath.Join(manager.layout.BinDir, "aria2-"+aria2Version+".zip")
	defer os.Remove(archive)
	if err := downloadFile(ctx, aria2WindowsURL, archive); err != nil {
		return "", fmt.Errorf("下载 aria2 失败: %w", err)
	}
	digest, err := sha256File(archive)
	if err != nil {
		return "", err
	}
	if digest != aria2WindowsSHA256 {
		return "", fmt.Errorf("aria2 下载包校验失败，已拒绝使用")
	}
	target := filepath.Join(manager.layout.BinDir, "aria2c.exe")
	reader, err := zip.OpenReader(archive)
	if err != nil {
		return "", err
	}
	defer reader.Close()
	for _, item := range reader.File {
		name := strings.ReplaceAll(item.Name, "\\", "/")
		if name == "aria2c.exe" || strings.HasSuffix(name, "/aria2c.exe") {
			if err := extractZipFile(item, target); err != nil {
				return "", err
			}
			logger.Infof("downloaded managed aria2c binary=%s", target)
			return target, nil
		}
	}
	return "", fmt.Errorf("aria2 下载包中未找到 aria2c.exe")
}

func downloadFile(ctx context.Context, source, target string) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, source, nil)
	if err != nil {
		return err
	}
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return fmt.Errorf("HTTP %d", response.StatusCode)
	}
	file, err := os.Create(target)
	if err != nil {
		return err
	}
	defer file.Close()
	_, err = io.Copy(file, response.Body)
	return err
}

func sha256File(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	digest := sha256.New()
	if _, err := io.Copy(digest, file); err != nil {
		return "", err
	}
	return fmt.Sprintf("%x", digest.Sum(nil)), nil
}

func extractZipFile(item *zip.File, target string) error {
	source, err := item.Open()
	if err != nil {
		return err
	}
	defer source.Close()
	output, err := os.Create(target)
	if err != nil {
		return err
	}
	defer output.Close()
	_, err = io.Copy(output, source)
	return err
}

func executableName(base string) string {
	if runtime.GOOS == "windows" {
		return base + ".exe"
	}
	return base
}
