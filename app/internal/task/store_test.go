package task

import (
	"testing"

	"smartclassdownloader/internal/domain"
)

func TestTerminalStateCannotRegress(t *testing.T) {
	store := NewStore()
	item := domain.NewTask("a", "title", domain.TaskKindCourse)
	item.Status = domain.TaskCompleted
	if err := store.Create(item); err != nil {
		t.Fatal(err)
	}
	running := domain.TaskRunning
	got, err := store.Update("a", domain.TaskUpdate{Status: &running})
	if err != nil {
		t.Fatal(err)
	}
	if got.Status != domain.TaskCompleted {
		t.Fatalf("terminal status regressed to %s", got.Status)
	}
}

func TestNextAvailableID(t *testing.T) {
	store := NewStore()
	_ = store.Create(domain.NewTask("video", "title", domain.TaskKindCourse))
	if got := store.NextAvailableID("video"); got != "video_1" {
		t.Fatalf("NextAvailableID() = %s", got)
	}
}
