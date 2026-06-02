package cleanup

import (
	"os"
	"path/filepath"
	"testing"
)

func TestCleanLogsRemovesEveryTopLevelFile(t *testing.T) {
	root := t.TempDir()
	nested := filepath.Join(root, "nested")
	if err := os.Mkdir(nested, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{
		filepath.Join(root, "server.log"),
		filepath.Join(root, "smartclass_go.log.1"),
		filepath.Join(root, "notes.txt"),
		filepath.Join(nested, "keep.log"),
	} {
		if err := os.WriteFile(path, []byte("old"), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	count, err := CleanLogs(root)
	if err != nil {
		t.Fatal(err)
	}
	if count != 3 {
		t.Fatalf("CleanLogs() count = %d, want 3", count)
	}
	for _, name := range []string{"server.log", "smartclass_go.log.1", "notes.txt"} {
		if _, err := os.Stat(filepath.Join(root, name)); !os.IsNotExist(err) {
			t.Fatalf("%s still exists: %v", name, err)
		}
	}
	if _, err := os.Stat(filepath.Join(nested, "keep.log")); err != nil {
		t.Fatalf("nested log should remain: %v", err)
	}
}
