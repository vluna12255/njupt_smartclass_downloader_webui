package plugin

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

//go:embed default_manifest.json
var defaultManifest []byte

type Definition struct {
	ID           string   `json:"id"`
	Folder       string   `json:"folder"`
	Entry        string   `json:"entry"`
	Venv         string   `json:"venv"`
	DefaultPort  int      `json:"default_port"`
	HealthPath   string   `json:"health_path"`
	Capabilities []string `json:"capabilities"`
	Requires     []string `json:"requires"`
	Requirements []string `json:"requirements"`
	ManagedPaths []string `json:"managed_paths"`
}

type Manifest struct {
	SchemaVersion int          `json:"schema_version"`
	Plugins       []Definition `json:"plugins"`
}

type Registry struct {
	definitions map[string]Definition
}

func LoadManifest(path string) (Manifest, error) {
	body, err := os.ReadFile(path)
	if err != nil {
		if !os.IsNotExist(err) {
			return Manifest{}, err
		}
		body = defaultManifest
	}
	var manifest Manifest
	if err := json.Unmarshal(body, &manifest); err != nil {
		return Manifest{}, err
	}
	return manifest, manifest.Validate()
}

func (manifest Manifest) Validate() error {
	if manifest.SchemaVersion != 1 {
		return fmt.Errorf("unsupported plugin manifest schema: %d", manifest.SchemaVersion)
	}
	seen := map[string]bool{}
	for _, item := range manifest.Plugins {
		if item.ID == "" || item.Folder == "" || item.Entry == "" || item.Venv == "" || item.DefaultPort < 1 {
			return fmt.Errorf("plugin manifest has incomplete definition")
		}
		if seen[item.ID] {
			return fmt.Errorf("duplicate plugin id: %s", item.ID)
		}
		seen[item.ID] = true
		for _, part := range append([]string{item.Folder, item.Entry, item.Venv}, item.ManagedPaths...) {
			clean := filepath.Clean(part)
			if clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) || filepath.IsAbs(clean) {
				return fmt.Errorf("plugin %s contains unsafe path: %s", item.ID, part)
			}
		}
	}
	return nil
}

func NewRegistry(manifest Manifest) (*Registry, error) {
	if err := manifest.Validate(); err != nil {
		return nil, err
	}
	registry := &Registry{definitions: map[string]Definition{}}
	for _, item := range manifest.Plugins {
		registry.definitions[item.ID] = item
	}
	return registry, nil
}

func (registry *Registry) Get(id string) (Definition, bool) {
	item, ok := registry.definitions[id]
	return item, ok
}

func (registry *Registry) List() []Definition {
	result := make([]Definition, 0, len(registry.definitions))
	for _, item := range registry.definitions {
		result = append(result, item)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].ID < result[j].ID })
	return result
}

func (registry *Registry) HasCapability(id, capability string) bool {
	item, ok := registry.Get(id)
	if !ok {
		return false
	}
	for _, value := range item.Capabilities {
		if value == capability {
			return true
		}
	}
	return false
}
