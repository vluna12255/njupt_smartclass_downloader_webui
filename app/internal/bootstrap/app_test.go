package bootstrap

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestNewApplicationPreservesPreviousRunLog(t *testing.T) {
	root := t.TempDir()
	for _, name := range []string{"templates", "static", "plugins", "logs"} {
		if err := os.MkdirAll(filepath.Join(root, name), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	logPath := filepath.Join(root, "logs", "smartclass_go.log")
	if err := os.WriteFile(logPath, []byte("previous run evidence\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	application, err := NewApplication(context.Background(), Options{RootDir: root})
	if err != nil {
		t.Fatal(err)
	}
	application.CloseLogs()

	body, err := os.ReadFile(logPath)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(body), "previous run evidence") {
		t.Fatalf("previous log evidence was removed: %q", body)
	}
}
