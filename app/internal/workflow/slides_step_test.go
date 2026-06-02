package workflow

import (
	"bytes"
	"context"
	"os"
	"testing"
	"time"

	"smartclassdownloader/internal/domain"
	"smartclassdownloader/internal/plugin"
)

func TestSlidesStepStartsAndChecksServiceInsideQueue(t *testing.T) {
	inSlot := false
	plugins := &recordingSlidesPlugins{inSlot: &inSlot}
	client := &recordingSlidesClient{inSlot: &inSlot}
	step := NewSlidesStep(plugins, client, recordingSlots{inSlot: &inSlot})
	var updates []domain.TaskUpdate

	err := step.Run(context.Background(), &Context{
		BaseDir: t.TempDir(), NeedPPT: true,
		Artifacts: map[string][]domain.VideoArtifact{"VGA": {{TrackType: "VGA", Path: "VGA.mp4"}}},
	}, func(update domain.TaskUpdate) {
		updates = append(updates, update)
	})
	if err != nil {
		t.Fatal(err)
	}
	if plugins.starts != 1 {
		t.Fatalf("plugin starts = %d, want 1", plugins.starts)
	}
	if client.healthChecks != 1 {
		t.Fatalf("health checks = %d, want 1", client.healthChecks)
	}
	if client.extractions != 1 {
		t.Fatalf("extractions = %d, want 1", client.extractions)
	}
	if len(updates) == 0 || updates[0].Status == nil || *updates[0].Status != domain.TaskWaiting {
		t.Fatalf("first update = %#v, want waiting queue status", updates)
	}
}

type recordingSlidesPlugins struct {
	inSlot *bool
	starts int
}

func (*recordingSlidesPlugins) IsInstalled(string) bool { return true }
func (plugins *recordingSlidesPlugins) Start(context.Context, string) (bool, error) {
	if !*plugins.inSlot {
		panic("plugin started outside PPT queue")
	}
	plugins.starts++
	return true, nil
}
func (*recordingSlidesPlugins) ServiceURL(string) (string, bool) {
	return "http://127.0.0.1:8002", true
}
func (*recordingSlidesPlugins) StartupError(string) error { return nil }

type recordingSlidesClient struct {
	inSlot       *bool
	healthChecks int
	extractions  int
}

func (client *recordingSlidesClient) WaitHealthy(context.Context, string, string, time.Duration, func() error) error {
	if !*client.inSlot {
		panic("health check ran outside PPT queue")
	}
	client.healthChecks++
	return nil
}

func (client *recordingSlidesClient) ExtractSlides(_ context.Context, _ string, request plugin.ExtractSlidesRequest) error {
	if !*client.inSlot {
		panic("extraction ran outside PPT queue")
	}
	client.extractions++
	return os.WriteFile(request.OutputPath, bytes.Repeat([]byte("x"), 1025), 0o644)
}

type recordingSlots struct {
	inSlot *bool
}

func (slots recordingSlots) WithDownloadSlot(_ context.Context, fn func() error) error { return fn() }
func (slots recordingSlots) WithASRSlot(_ context.Context, fn func() error) error      { return fn() }
func (slots recordingSlots) WithSlidesSlot(_ context.Context, fn func() error) error {
	*slots.inSlot = true
	defer func() { *slots.inSlot = false }()
	return fn()
}
