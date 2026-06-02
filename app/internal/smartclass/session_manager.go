package smartclass

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"smartclassdownloader/internal/config"
)

type SessionManager struct {
	mu         sync.RWMutex
	config     *config.Service
	httpClient *http.Client
	client     *Client
}

func NewSessionManager(service *config.Service) *SessionManager {
	return &SessionManager{config: service}
}

func (manager *SessionManager) Login(ctx context.Context, username, password string) error {
	timeout := time.Duration(manager.config.Current().NetworkTimeoutSeconds) * time.Second
	httpClient, err := NewSessionHTTPClient(timeout)
	if err != nil {
		return err
	}
	sso := NewSSOClient(httpClient)
	if err := sso.Login(ctx, username, password); err != nil {
		return err
	}
	if err := sso.GrantService(ctx, smartclassBaseURL+"/Login/SSO"); err != nil {
		return err
	}
	request, _ := http.NewRequestWithContext(ctx, http.MethodGet, smartclassBaseURL+"/", nil)
	ApplyDefaultHeaders(request)
	if response, err := httpClient.Do(request); err == nil {
		_ = response.Body.Close()
	}
	client := NewClient(httpClient, manager.config)
	if _, err := client.CSRKToken(ctx); err != nil {
		return err
	}
	manager.mu.Lock()
	manager.httpClient = httpClient
	manager.client = client
	manager.mu.Unlock()
	return nil
}

func (manager *SessionManager) AutoLogin(ctx context.Context) error {
	username, password, ok, err := manager.config.Credentials(ctx)
	if err != nil {
		return err
	}
	if !ok {
		return fmt.Errorf("无保存的凭证")
	}
	return manager.Login(ctx, username, password)
}

func (manager *SessionManager) Client(ctx context.Context) (*Client, error) {
	manager.mu.RLock()
	client := manager.client
	manager.mu.RUnlock()
	if client != nil && manager.IsValid(ctx) {
		return client, nil
	}
	if manager.config.Current().AutoLogin {
		if err := manager.AutoLogin(ctx); err == nil {
			manager.mu.RLock()
			defer manager.mu.RUnlock()
			return manager.client, nil
		}
	}
	return nil, fmt.Errorf("登录已失效")
}

// ClientForOperation returns the active client without an extra network probe.
// The operation itself will surface an expired session while avoiding a slow
// round trip before every search or download request.
func (manager *SessionManager) ClientForOperation(ctx context.Context) (*Client, error) {
	manager.mu.RLock()
	client := manager.client
	manager.mu.RUnlock()
	if client != nil {
		return client, nil
	}
	if manager.config.Current().AutoLogin {
		if err := manager.AutoLogin(ctx); err == nil {
			manager.mu.RLock()
			defer manager.mu.RUnlock()
			return manager.client, nil
		}
	}
	return nil, fmt.Errorf("登录已失效")
}

func (manager *SessionManager) HTTPClient(ctx context.Context) (*http.Client, error) {
	if _, err := manager.Client(ctx); err != nil {
		return nil, err
	}
	manager.mu.RLock()
	defer manager.mu.RUnlock()
	return manager.httpClient, nil
}

func (manager *SessionManager) IsValid(ctx context.Context) bool {
	manager.mu.RLock()
	current := manager.httpClient
	manager.mu.RUnlock()
	if current == nil {
		return false
	}
	probe := *current
	probe.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	request, _ := http.NewRequestWithContext(ctx, http.MethodGet, smartclassBaseURL+"/", nil)
	ApplyDefaultHeaders(request)
	response, err := probe.Do(request)
	if err != nil {
		return false
	}
	defer response.Body.Close()
	return response.StatusCode != http.StatusFound || !strings.Contains(strings.ToLower(response.Header.Get("Location")), "login")
}

func (manager *SessionManager) Clear() {
	manager.mu.Lock()
	manager.client = nil
	manager.httpClient = nil
	manager.mu.Unlock()
}

func HeadersAndCookies(client *http.Client, target string) []string {
	headers := []string{
		"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
		"Referer: " + smartclassBaseURL + "/",
	}
	parsed, err := url.Parse(target)
	if err != nil || client == nil || client.Jar == nil {
		return headers
	}
	cookies := client.Jar.Cookies(parsed)
	parts := make([]string, 0, len(cookies))
	for _, cookie := range cookies {
		parts = append(parts, cookie.Name+"="+cookie.Value)
	}
	if len(parts) > 0 {
		headers = append(headers, "Cookie: "+strings.Join(parts, "; "))
	}
	return headers
}
