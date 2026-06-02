package plugin

import (
	"context"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"smartclassdownloader/internal/platform"
)

func TestInstallLocalWheelsUsesBundledFunASRWheel(t *testing.T) {
	binDir := t.TempDir()
	wheel := filepath.Join(binDir, editdistanceWheel)
	if err := os.WriteFile(wheel, []byte("wheel"), 0o644); err != nil {
		t.Fatal(err)
	}
	installer := &Installer{layout: platform.Layout{BinDir: binDir}}
	original := runInstallCommand
	defer func() { runInstallCommand = original }()
	var gotArgs []string
	runInstallCommand = func(_ context.Context, _ string, args []string, _ func(string), _ io.Writer) error {
		gotArgs = append([]string{}, args...)
		return nil
	}

	err := installer.installLocalWheels(context.Background(), "funasr", "python.exe", func(string) {}, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Join(gotArgs, " ") != "-m pip install "+wheel {
		t.Fatalf("local wheel args = %q", gotArgs)
	}
}

func TestTailWriterKeepsRecentOutput(t *testing.T) {
	writer := &tailWriter{}
	if _, err := writer.Write([]byte(strings.Repeat("a", 4096) + "tail")); err != nil {
		t.Fatal(err)
	}
	if got := writer.String(); len(got) != 4096 || !strings.HasSuffix(got, "tail") {
		t.Fatalf("tailWriter output length=%d suffix=%q", len(got), got[len(got)-4:])
	}
}
