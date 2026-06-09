package config

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"

	"smartclassdownloader/internal/platform"
)

type AuthMetadata struct {
	Username   string `json:"username"`
	UseKeyring bool   `json:"use_keyring"`
	Password   string `json:"password,omitempty"`
}

type Store struct {
	layout       platform.Layout
	settingsPath string
	authPath     string
}

func NewStore(layout platform.Layout) *Store {
	return &Store{
		layout: layout, settingsPath: filepath.Join(layout.ConfigDir, "settings.json"),
		authPath: filepath.Join(layout.ConfigDir, "auth.json"),
	}
}

func (store *Store) LoadSettings() (Settings, error) {
	defaults := DefaultSettings(store.layout)
	body, err := os.ReadFile(store.settingsPath)
	if errors.Is(err, os.ErrNotExist) {
		return defaults, nil
	}
	if err != nil {
		return Settings{}, err
	}
	settings := defaults
	if err := json.Unmarshal(body, &settings); err != nil {
		return Settings{}, err
	}
	return Normalize(settings, store.layout), nil
}

func (store *Store) SaveSettings(settings Settings) error {
	return atomicJSON(store.settingsPath, settings)
}

func (store *Store) LoadAuthMetadata() (AuthMetadata, error) {
	body, err := os.ReadFile(store.authPath)
	if errors.Is(err, os.ErrNotExist) {
		return AuthMetadata{}, nil
	}
	if err != nil {
		return AuthMetadata{}, err
	}
	var metadata AuthMetadata
	if err := json.Unmarshal(body, &metadata); err != nil {
		return AuthMetadata{}, err
	}
	return metadata, nil
}

func (store *Store) SaveAuthMetadata(metadata AuthMetadata) error {
	metadata.Password = ""
	return atomicJSON(store.authPath, metadata)
}

func atomicJSON(path string, value any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	body, err := json.MarshalIndent(value, "", "    ")
	if err != nil {
		return err
	}
	body = append(body, '\n')
	temp, err := os.CreateTemp(filepath.Dir(path), ".settings-*.tmp")
	if err != nil {
		return err
	}
	tempName := temp.Name()
	defer os.Remove(tempName)
	if _, err := temp.Write(body); err != nil {
		_ = temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	return os.Rename(tempName, path)
}
