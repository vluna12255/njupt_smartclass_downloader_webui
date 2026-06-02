package platform

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

type Layout struct {
	RootDir         string
	ConfigDir       string
	LogsDir         string
	PluginsDir      string
	PluginsEnvDir   string
	RuntimeDir      string
	PluginStatusDir string
	BinDir          string
	DownloadsDir    string
	TemplatesDir    string
	StaticDir       string
}

func ResolveLayout(rootOverride string) (Layout, error) {
	root := strings.TrimSpace(rootOverride)
	if root == "" {
		cwd, err := os.Getwd()
		if err != nil {
			return Layout{}, err
		}
		root = cwd
		if !looksLikeRoot(root) && looksLikeRoot(filepath.Dir(root)) {
			root = filepath.Dir(root)
		}
		if !looksLikeRoot(root) {
			executable, err := os.Executable()
			if err != nil {
				return Layout{}, err
			}
			root = filepath.Dir(executable)
		}
	}
	root, err := filepath.Abs(root)
	if err != nil {
		return Layout{}, err
	}
	return Layout{
		RootDir:         root,
		ConfigDir:       filepath.Join(root, "config"),
		LogsDir:         filepath.Join(root, "logs"),
		PluginsDir:      filepath.Join(root, "plugins"),
		PluginsEnvDir:   filepath.Join(root, "plugins_env"),
		RuntimeDir:      filepath.Join(root, "runtime"),
		PluginStatusDir: filepath.Join(root, "runtime", "plugin_status"),
		BinDir:          filepath.Join(root, "bin"),
		DownloadsDir:    filepath.Join(root, "SmartclassDownload"),
		TemplatesDir:    filepath.Join(root, "templates"),
		StaticDir:       filepath.Join(root, "static"),
	}, nil
}

func looksLikeRoot(path string) bool {
	for _, name := range []string{"templates", "static", "plugins"} {
		if info, err := os.Stat(filepath.Join(path, name)); err != nil || !info.IsDir() {
			return false
		}
	}
	return true
}

func (l Layout) EnsureMutableDirs() error {
	for _, path := range []string{
		l.ConfigDir, l.LogsDir, l.PluginsEnvDir, l.RuntimeDir,
		l.PluginStatusDir, l.BinDir, l.DownloadsDir,
	} {
		if err := os.MkdirAll(path, 0o755); err != nil {
			return fmt.Errorf("create %s: %w", path, err)
		}
	}
	return nil
}

func (l Layout) PluginVenv(name string) string {
	return filepath.Join(l.PluginsEnvDir, name)
}

func (l Layout) PluginStatusFile(name string) string {
	return filepath.Join(l.PluginStatusDir, name+".json")
}

func SafeJoin(root string, elems ...string) (string, error) {
	rootAbs, err := filepath.Abs(root)
	if err != nil {
		return "", err
	}
	target, err := filepath.Abs(filepath.Join(append([]string{rootAbs}, elems...)...))
	if err != nil {
		return "", err
	}
	rel, err := filepath.Rel(rootAbs, target)
	if err != nil {
		return "", err
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) || filepath.IsAbs(rel) {
		return "", fmt.Errorf("path escapes managed root: %s", target)
	}
	return target, nil
}
