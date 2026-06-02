package applog

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestLoggerWritesComponentAndHonorsLevel(t *testing.T) {
	var console bytes.Buffer
	manager, err := New(Config{
		Dir: t.TempDir(), FileName: "test.log", Level: "warn", Console: &console,
		Now: func() time.Time { return time.Date(2026, 6, 2, 17, 30, 0, 0, time.Local) },
	})
	if err != nil {
		t.Fatal(err)
	}
	defer manager.Close()
	logger := &Logger{component: "test"}

	manager.log(LevelInfo, logger.component, "hidden")
	manager.log(LevelWarn, logger.component, "visible")

	got := console.String()
	if strings.Contains(got, "hidden") {
		t.Fatalf("console contains filtered message: %q", got)
	}
	if !strings.Contains(got, "[17:30:00] WARN    [test] visible") {
		t.Fatalf("console output = %q", got)
	}
}

func TestRotatingFileWriterKeepsBoundedBackups(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rotate.log")
	writer, err := newRotatingFileWriter(path, 8, 2)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := writer.Write([]byte("first\n")); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.Write([]byte("second\n")); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.Write([]byte("third\n")); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}

	assertFileContent(t, path, "third\n")
	assertFileContent(t, path+".1", "second\n")
	assertFileContent(t, path+".2", "first\n")
	if _, err := os.Stat(path + ".3"); !os.IsNotExist(err) {
		t.Fatalf("unexpected third backup: %v", err)
	}
}

func assertFileContent(t *testing.T, path, want string) {
	t.Helper()
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(body) != want {
		t.Fatalf("%s = %q, want %q", path, body, want)
	}
}
