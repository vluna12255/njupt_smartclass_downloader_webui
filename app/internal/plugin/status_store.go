package plugin

import (
	"encoding/json"
	"os"
	"sync"
	"time"

	"smartclassdownloader/internal/platform"
)

type RuntimeStatus struct {
	Phase          string         `json:"phase"`
	Progress       float64        `json:"progress"`
	Message        string         `json:"message"`
	Error          string         `json:"error"`
	Timestamp      float64        `json:"timestamp"`
	Device         string         `json:"device"`
	TotalSize      int64          `json:"total_size"`
	DownloadedSize int64          `json:"downloaded_size"`
	Speed          float64        `json:"speed"`
	Details        map[string]any `json:"-"`
}

type StatusStore struct {
	mu     sync.RWMutex
	layout platform.Layout
	cache  map[string]RuntimeStatus
}

func NewStatusStore(layout platform.Layout) *StatusStore {
	return &StatusStore{layout: layout, cache: map[string]RuntimeStatus{}}
}

func (store *StatusStore) Read(pluginID string) RuntimeStatus {
	body, err := os.ReadFile(store.layout.PluginStatusFile(pluginID))
	if err == nil {
		var raw map[string]any
		if json.Unmarshal(body, &raw) == nil {
			status := runtimeStatusFromMap(raw)
			store.Update(pluginID, status)
		}
	}
	store.mu.RLock()
	defer store.mu.RUnlock()
	return store.cache[pluginID]
}

func (store *StatusStore) Update(pluginID string, status RuntimeStatus) {
	if status.Timestamp == 0 {
		status.Timestamp = float64(time.Now().UnixMilli()) / 1000
	}
	store.mu.Lock()
	store.cache[pluginID] = status
	store.mu.Unlock()
}

func (store *StatusStore) Clear(pluginID string) error {
	store.mu.Lock()
	delete(store.cache, pluginID)
	store.mu.Unlock()
	err := os.Remove(store.layout.PluginStatusFile(pluginID))
	if os.IsNotExist(err) {
		return nil
	}
	return err
}

func runtimeStatusFromMap(raw map[string]any) RuntimeStatus {
	status := RuntimeStatus{Details: raw}
	status.Phase, _ = raw["phase"].(string)
	status.Message, _ = raw["message"].(string)
	status.Error, _ = raw["error"].(string)
	status.Device, _ = raw["device"].(string)
	status.Progress, _ = raw["progress"].(float64)
	status.Timestamp, _ = raw["timestamp"].(float64)
	status.TotalSize = int64(number(raw["total_size"]))
	status.DownloadedSize = int64(number(raw["downloaded_size"]))
	status.Speed = number(raw["speed"])
	return status
}

func number(value any) float64 {
	result, _ := value.(float64)
	return result
}
