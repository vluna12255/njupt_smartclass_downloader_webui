package plugin

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"

	"smartclassdownloader/internal/platform"
)

const (
	pythonPortableURL = "https://github.com/astral-sh/python-build-standalone/releases/download/20260114/cpython-3.13.11+20260114-x86_64-pc-windows-msvc-install_only.tar.gz"
	ffmpegURL         = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
	vcRedistURL       = "https://aka.ms/vc14/vc_redist.x64.exe"
	pipIndexURL       = "https://mirrors.aliyun.com/pypi/simple/"
	editdistanceWheel = "editdistance-0.8.1-cp313-cp313-win_amd64.whl"
)

var runInstallCommand = run

type Installer struct {
	layout   platform.Layout
	registry *Registry
}

func NewInstaller(layout platform.Layout, registry *Registry) *Installer {
	return &Installer{layout: layout, registry: registry}
}

func (installer *Installer) IsInstalled(pluginID string) bool {
	definition, ok := installer.registry.Get(pluginID)
	if !ok {
		return false
	}
	_, err := os.Stat(filepath.Join(installer.layout.PluginVenv(definition.Venv), ".install_success"))
	return err == nil
}

func (installer *Installer) Install(ctx context.Context, pluginID string, report func(string)) (err error) {
	definition, ok := installer.registry.Get(pluginID)
	if !ok {
		return fmt.Errorf("未知插件: %s", pluginID)
	}
	if installer.IsInstalled(pluginID) {
		report("插件环境已存在，跳过重复安装")
		return nil
	}
	installLogPath := filepath.Join(installer.layout.LogsDir, pluginID+"_install.log")
	installLog, err := os.OpenFile(installLogPath, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o644)
	if err != nil {
		return fmt.Errorf("创建安装日志: %w", err)
	}
	defer installLog.Close()
	defer func() {
		if err != nil {
			logger.Errorf("install plugin id=%s: %v (log=%s)", pluginID, err, installLogPath)
			err = fmt.Errorf("%w（安装日志: %s）", err, installLogPath)
		}
	}()
	logger.Infof("installing plugin id=%s log=%s", pluginID, installLogPath)
	report("正在准备 Python 运行环境...")
	python, err := installer.ensurePython(ctx)
	if err != nil {
		return err
	}
	if contains(definition.Requires, "vc_redist") {
		report("正在检查 VC++ Runtime...")
		if err := installer.ensureVCRedist(ctx); err != nil {
			return err
		}
	}
	if contains(definition.Requires, "ffmpeg") {
		report("正在准备 FFmpeg...")
		if err := installer.ensureFFmpeg(ctx); err != nil {
			return err
		}
	}
	venv := installer.layout.PluginVenv(definition.Venv)
	report("正在创建独立虚拟环境...")
	if err = runInstallCommand(ctx, python, []string{"-m", "venv", venv}, report, installLog); err != nil {
		return err
	}
	venvPython := filepath.Join(venv, "Scripts", "python.exe")
	report("正在升级构建工具...")
	if err = runInstallCommand(ctx, venvPython, []string{"-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel", "-i", pipIndexURL}, report, installLog); err != nil {
		return err
	}
	if err = installer.installLocalWheels(ctx, pluginID, venvPython, report, installLog); err != nil {
		return err
	}
	if contains(definition.Requires, "torch") {
		report("正在配置 PyTorch 计算库...")
		index := "https://download.pytorch.org/whl/cpu"
		if _, lookupErr := exec.LookPath("nvidia-smi"); lookupErr == nil {
			index = "https://download.pytorch.org/whl/cu124"
		}
		if err = runInstallCommand(ctx, venvPython, []string{"-m", "pip", "install", "torch", "torchaudio", "--index-url", index}, report, installLog); err != nil {
			return err
		}
	}
	report("正在安装插件依赖组件...")
	args := []string{"-m", "pip", "install", "--prefer-binary", "-i", pipIndexURL}
	args = append(args, definition.Requirements...)
	if err = runInstallCommand(ctx, venvPython, args, report, installLog); err != nil {
		return err
	}
	if err = os.WriteFile(filepath.Join(venv, ".install_success"), []byte("ok\n"), 0o644); err != nil {
		return err
	}
	report(pluginID + " 安装成功")
	logger.Infof("installed plugin id=%s", pluginID)
	return nil
}

func (installer *Installer) installLocalWheels(ctx context.Context, pluginID, python string, report func(string), output io.Writer) error {
	if pluginID != "funasr" {
		return nil
	}
	wheel := filepath.Join(installer.layout.BinDir, editdistanceWheel)
	if _, err := os.Stat(wheel); err != nil {
		if os.IsNotExist(err) {
			logger.Warnf("optional local wheel not found plugin=%s wheel=%s", pluginID, wheel)
			return nil
		}
		return err
	}
	report("正在安装 FunASR 的 Python 3.13 预编译组件...")
	return runInstallCommand(ctx, python, []string{"-m", "pip", "install", wheel}, report, output)
}

func (installer *Installer) Uninstall(_ context.Context, pluginID string) error {
	definition, ok := installer.registry.Get(pluginID)
	if !ok {
		return fmt.Errorf("未知插件: %s", pluginID)
	}
	venv, err := platform.SafeJoin(installer.layout.PluginsEnvDir, definition.Venv)
	if err != nil {
		return err
	}
	if err := os.RemoveAll(venv); err != nil {
		return err
	}
	pluginRoot, err := platform.SafeJoin(installer.layout.PluginsDir, definition.Folder)
	if err != nil {
		return err
	}
	for _, relative := range definition.ManagedPaths {
		target, err := platform.SafeJoin(pluginRoot, relative)
		if err != nil {
			return err
		}
		if err := os.RemoveAll(target); err != nil {
			return err
		}
	}
	return nil
}

func (installer *Installer) ensurePython(ctx context.Context) (string, error) {
	candidates := []string{
		filepath.Join(installer.layout.RuntimeDir, "python.exe"),
		filepath.Join(installer.layout.RuntimeDir, "python", "python.exe"),
	}
	for _, candidate := range candidates {
		if _, err := os.Stat(candidate); err == nil {
			return candidate, nil
		}
	}
	if runtime.GOOS != "windows" {
		return "", fmt.Errorf("未找到便携 Python 运行时")
	}
	archive := filepath.Join(installer.layout.RuntimeDir, "python_runtime.tar.gz")
	defer os.Remove(archive)
	if err := fetch(ctx, pythonPortableURL, archive); err != nil {
		return "", err
	}
	if err := extractTarGz(archive, installer.layout.RuntimeDir); err != nil {
		return "", err
	}
	for _, candidate := range candidates {
		if _, err := os.Stat(candidate); err == nil {
			return candidate, nil
		}
	}
	return "", fmt.Errorf("解压后未找到 python.exe")
}

func (installer *Installer) ensureFFmpeg(ctx context.Context) error {
	target := filepath.Join(installer.layout.BinDir, "ffmpeg.exe")
	if _, err := os.Stat(target); err == nil {
		return nil
	}
	archive := filepath.Join(installer.layout.BinDir, "ffmpeg_temp.zip")
	defer os.Remove(archive)
	if err := fetch(ctx, ffmpegURL, archive); err != nil {
		return err
	}
	reader, err := zip.OpenReader(archive)
	if err != nil {
		return err
	}
	defer reader.Close()
	for _, item := range reader.File {
		if strings.HasSuffix(strings.ReplaceAll(item.Name, "\\", "/"), "/bin/ffmpeg.exe") {
			return extractZipFile(item, target)
		}
	}
	return fmt.Errorf("FFmpeg 压缩包中未找到 ffmpeg.exe")
}

func (installer *Installer) ensureVCRedist(ctx context.Context) error {
	installed, err := platform.IsVCRedistInstalled()
	if err != nil || installed {
		return err
	}
	target := filepath.Join(installer.layout.RuntimeDir, "vc_redist.x64.exe")
	defer os.Remove(target)
	if err := fetch(ctx, vcRedistURL, target); err != nil {
		return err
	}
	process, err := platform.StartManagedProcess(ctx, platform.CommandSpec{
		Path: target, Args: []string{"/install", "/passive", "/norestart"}, Env: platform.BaseEnvironment(nil),
	})
	if err != nil {
		return err
	}
	return process.Wait()
}

func extractTarGz(path, targetRoot string) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	gzipReader, err := gzip.NewReader(file)
	if err != nil {
		return err
	}
	defer gzipReader.Close()
	reader := tar.NewReader(gzipReader)
	for {
		header, err := reader.Next()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		target, err := platform.SafeJoin(targetRoot, header.Name)
		if err != nil {
			return err
		}
		switch header.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
		case tar.TypeReg:
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			output, err := os.Create(target)
			if err != nil {
				return err
			}
			_, copyErr := io.Copy(output, reader)
			closeErr := output.Close()
			if copyErr != nil {
				return copyErr
			}
			if closeErr != nil {
				return closeErr
			}
		}
	}
}

func fetch(ctx context.Context, source, target string) error {
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
		return fmt.Errorf("download HTTP %d", response.StatusCode)
	}
	output, err := os.Create(target)
	if err != nil {
		return err
	}
	defer output.Close()
	_, err = io.Copy(output, response.Body)
	return err
}

func extractZipFile(item *zip.File, target string) error {
	source, err := item.Open()
	if err != nil {
		return err
	}
	defer source.Close()
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return err
	}
	output, err := os.Create(target)
	if err != nil {
		return err
	}
	defer output.Close()
	_, err = io.Copy(output, source)
	return err
}

func run(ctx context.Context, path string, args []string, report func(string), output io.Writer) error {
	report("执行: " + filepath.Base(path) + " " + strings.Join(args[:min(len(args), 4)], " "))
	tail := &tailWriter{}
	commandOutput := io.Writer(tail)
	if output != nil {
		commandOutput = io.MultiWriter(output, tail)
	}
	process, err := platform.StartManagedProcess(ctx, platform.CommandSpec{
		Path: path, Args: args, Env: platform.BaseEnvironment(map[string]string{"PYTHONUNBUFFERED": "1"}), Hidden: true,
		Stdout: commandOutput, Stderr: commandOutput,
	})
	if err != nil {
		return err
	}
	if err := process.Wait(); err != nil {
		detail := strings.TrimSpace(tail.String())
		if detail == "" {
			return fmt.Errorf("%s failed: %w", filepath.Base(path), err)
		}
		return fmt.Errorf("%s failed: %w\n%s", filepath.Base(path), err, detail)
	}
	return nil
}

type tailWriter struct {
	mu   sync.Mutex
	body []byte
}

func (writer *tailWriter) Write(body []byte) (int, error) {
	writer.mu.Lock()
	defer writer.mu.Unlock()
	writer.body = append(writer.body, body...)
	const maxBytes = 4096
	if len(writer.body) > maxBytes {
		writer.body = append([]byte{}, writer.body[len(writer.body)-maxBytes:]...)
	}
	return len(body), nil
}

func (writer *tailWriter) String() string {
	writer.mu.Lock()
	defer writer.mu.Unlock()
	return string(writer.body)
}

func contains(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

func min(left, right int) int {
	if left < right {
		return left
	}
	return right
}
