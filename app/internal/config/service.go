package config

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"sync"

	"smartclassdownloader/internal/platform"
)

const credentialService = "SmartClassDownloader"

type Service struct {
	mu          sync.RWMutex
	layout      platform.Layout
	store       *Store
	credentials platform.CredentialStore
	settings    Settings
}

func NewService(layout platform.Layout, store *Store, credentials platform.CredentialStore) *Service {
	return &Service{layout: layout, store: store, credentials: credentials}
}

func (service *Service) Load(ctx context.Context) error {
	settings, err := service.store.LoadSettings()
	if err != nil {
		return err
	}
	settings = Normalize(settings, service.layout)
	if err := os.MkdirAll(settings.DownloadDir, 0o755); err != nil {
		return err
	}
	service.mu.Lock()
	service.settings = settings
	service.mu.Unlock()
	if err := service.store.SaveSettings(settings); err != nil {
		return err
	}
	return service.migrateLegacyCredential(ctx)
}

func (service *Service) Current() Settings {
	service.mu.RLock()
	defer service.mu.RUnlock()
	return service.settings
}

func (service *Service) Save(ctx context.Context, patch map[string]any) (Settings, error) {
	current := service.Current()
	body, err := json.Marshal(patch)
	if err != nil {
		return Settings{}, err
	}
	if err := json.Unmarshal(body, &current); err != nil {
		return Settings{}, err
	}
	current = Normalize(current, service.layout)
	if err := current.Validate(); err != nil {
		return Settings{}, err
	}
	username, _ := patch["username"].(string)
	password, _ := patch["password"].(string)
	if current.AutoLogin && username != "" && password != "" {
		if err := service.SaveCredentials(ctx, username, password); err != nil {
			return Settings{}, err
		}
	}
	if err := os.MkdirAll(current.DownloadDir, 0o755); err != nil {
		return Settings{}, err
	}
	if err := service.store.SaveSettings(current); err != nil {
		return Settings{}, err
	}
	service.mu.Lock()
	service.settings = current
	service.mu.Unlock()
	return current, nil
}

func (service *Service) SaveCredentials(ctx context.Context, username, password string) error {
	if username == "" || password == "" {
		return fmt.Errorf("用户名和密码不能为空")
	}
	if err := service.credentials.Set(ctx, credentialService, username, password); err != nil {
		return err
	}
	return service.store.SaveAuthMetadata(AuthMetadata{Username: username, UseKeyring: true})
}

func (service *Service) Credentials(ctx context.Context) (string, string, bool, error) {
	metadata, err := service.store.LoadAuthMetadata()
	if err != nil || metadata.Username == "" {
		return "", "", false, err
	}
	password, ok, err := service.credentials.Get(ctx, credentialService, metadata.Username)
	return metadata.Username, password, ok && password != "", err
}

func (service *Service) PublicView(ctx context.Context) map[string]any {
	settings := service.Current()
	body, _ := json.Marshal(settings)
	result := map[string]any{}
	_ = json.Unmarshal(body, &result)
	metadata, _ := service.store.LoadAuthMetadata()
	_, _, saved, _ := service.Credentials(ctx)
	if metadata.Username != "" {
		result["username"] = metadata.Username
		result["auth"] = map[string]any{"username": metadata.Username, "credential_saved": saved}
	}
	result["credential_saved"] = saved
	return result
}

func (service *Service) migrateLegacyCredential(ctx context.Context) error {
	metadata, err := service.store.LoadAuthMetadata()
	if err != nil || metadata.Username == "" || metadata.Password == "" {
		return err
	}
	if err := service.credentials.Set(ctx, credentialService, metadata.Username, metadata.Password); err != nil {
		return err
	}
	return service.store.SaveAuthMetadata(AuthMetadata{Username: metadata.Username, UseKeyring: true})
}
