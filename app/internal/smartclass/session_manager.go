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
	mu               sync.RWMutex
	loginMu          sync.Mutex
	config           *config.Service
	newSessionClient func(time.Duration) (*http.Client, error)
	httpClient       *http.Client
	client           *Client
}

func NewSessionManager(service *config.Service) *SessionManager {
	return &SessionManager{config: service, newSessionClient: NewSessionHTTPClient}
}

func (manager *SessionManager) Login(ctx context.Context, username, password string) error {
	manager.loginMu.Lock()
	defer manager.loginMu.Unlock()
	return manager.loginLocked(ctx, username, password)
}

func (manager *SessionManager) loginLocked(ctx context.Context, username, password string) error {
	timeout := time.Duration(manager.config.Current().NetworkTimeoutSeconds) * time.Second
	factory := manager.newSessionClient
	if factory == nil {
		factory = NewSessionHTTPClient
	}
	httpClient, err := factory(timeout)
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
	manager.loginMu.Lock()
	defer manager.loginMu.Unlock()
	return manager.autoLoginLocked(ctx)
}

func (manager *SessionManager) autoLoginLocked(ctx context.Context) error {
	username, password, ok, err := manager.config.Credentials(ctx)
	if err != nil {
		return err
	}
	if !ok {
		return fmt.Errorf("无保存的凭证")
	}
	return manager.loginLocked(ctx, username, password)
}

func (manager *SessionManager) Client(ctx context.Context) (*Client, error) {
	client, httpClient := manager.currentPair()
	if client != nil && manager.isValid(ctx, httpClient) {
		return client, nil
	}
	if !manager.config.Current().AutoLogin {
		return nil, fmt.Errorf("登录已失效")
	}

	manager.loginMu.Lock()
	defer manager.loginMu.Unlock()

	client, httpClient = manager.currentPair()
	if client != nil && manager.isValid(ctx, httpClient) {
		return client, nil
	}
	if err := manager.autoLoginLocked(ctx); err == nil {
		client, _ = manager.currentPair()
		return client, nil
	}
	return nil, fmt.Errorf("登录已失效")
}

// ClientForOperation validates the active session before a SmartClass operation.
func (manager *SessionManager) ClientForOperation(ctx context.Context) (*Client, error) {
	return manager.Client(ctx)
}

func (manager *SessionManager) HTTPClient(ctx context.Context) (*http.Client, error) {
	_, httpClient, err := manager.ClientPair(ctx)
	if err != nil {
		return nil, err
	}
	return httpClient, nil
}

func (manager *SessionManager) ClientPair(ctx context.Context) (*Client, *http.Client, error) {
	if _, err := manager.Client(ctx); err != nil {
		return nil, nil, err
	}
	manager.mu.RLock()
	defer manager.mu.RUnlock()
	if manager.client == nil || manager.httpClient == nil {
		return nil, nil, fmt.Errorf("登录已失效")
	}
	return manager.client, manager.httpClient, nil
}

func (manager *SessionManager) IsValid(ctx context.Context) bool {
	_, current := manager.currentPair()
	return manager.isValid(ctx, current)
}

func (manager *SessionManager) isValid(ctx context.Context, current *http.Client) bool {
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

func (manager *SessionManager) currentPair() (*Client, *http.Client) {
	manager.mu.RLock()
	defer manager.mu.RUnlock()
	return manager.client, manager.httpClient
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
