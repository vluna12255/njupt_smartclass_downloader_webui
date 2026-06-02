package platform

import (
	"path/filepath"
	"testing"
)

func TestSafeJoinRejectsEscape(t *testing.T) {
	root := t.TempDir()
	if _, err := SafeJoin(root, "..", "outside"); err == nil {
		t.Fatal("expected path escape error")
	}
	if _, err := SafeJoin(root, "inside", "file.txt"); err != nil {
		t.Fatal(err)
	}
}

func TestLayoutPluginStatusFile(t *testing.T) {
	layout := Layout{PluginStatusDir: filepath.Join("runtime", "plugin_status")}
	if got := layout.PluginStatusFile("whisper"); got != filepath.Join("runtime", "plugin_status", "whisper.json") {
		t.Fatalf("unexpected status path: %s", got)
	}
}
