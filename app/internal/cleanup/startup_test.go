package cleanup

import (
	"os"
	"path/filepath"
	"testing"

	"smartclassdownloader/internal/config"
	"smartclassdownloader/internal/platform"
)

type staticSettings struct {
	settings config.Settings
}

func (provider staticSettings) Current() config.Settings {
	return provider.settings
}

func TestStartupCleanupRemovesTemporaryDownloadsWithoutTouchingLogs(t *testing.T) {
	root := t.TempDir()
	layout, err := platform.ResolveLayout(root)
	if err != nil {
		t.Fatal(err)
	}
	if err := layout.EnsureMutableDirs(); err != nil {
		t.Fatal(err)
	}
	temporary := filepath.Join(layout.DownloadsDir, "video.tmp")
	download := filepath.Join(layout.DownloadsDir, "video.mp4")
	logPath := filepath.Join(layout.LogsDir, "smartclass_go.log")
	for _, path := range []string{temporary, download, logPath} {
		if err := os.WriteFile(path, []byte("data"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	service := NewService(layout, staticSettings{settings: config.Settings{DownloadDir: layout.DownloadsDir}})

	removed, err := service.CleanTemporaryDownloads()
	if err != nil {
		t.Fatal(err)
	}
	if removed != 1 {
		t.Fatalf("removed = %d, want 1", removed)
	}
	if _, err := os.Stat(temporary); !os.IsNotExist(err) {
		t.Fatalf("temporary download still exists: %v", err)
	}
	for _, path := range []string{download, logPath} {
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("retained file missing: %s: %v", path, err)
		}
	}
}
