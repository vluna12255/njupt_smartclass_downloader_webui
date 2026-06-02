package smartclass

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"sync"
	"time"

	"smartclassdownloader/internal/config"
	"smartclassdownloader/internal/domain"
)

const smartclassBaseURL = "https://njupt.smartclass.cn"

type ConfigProvider interface {
	Current() config.Settings
}

type Client struct {
	httpClient    *http.Client
	config        ConfigProvider
	baseURL       string
	csrkMu        sync.Mutex
	cachedCSRKKey string
	csrkExpiresAt time.Time
}

func NewClient(httpClient *http.Client, provider ConfigProvider) *Client {
	return &Client{httpClient: httpClient, config: provider, baseURL: smartclassBaseURL}
}

func NewSessionHTTPClient(timeout time.Duration) (*http.Client, error) {
	jar, err := newCookieJar()
	if err != nil {
		return nil, err
	}
	return &http.Client{Timeout: timeout, Jar: jar}, nil
}

func ApplyDefaultHeaders(request *http.Request) {
	request.Header.Set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
	request.Header.Set("Referer", smartclassBaseURL+"/")
	request.Header.Set("Origin", smartclassBaseURL)
	request.Header.Set("X-Requested-With", "XMLHttpRequest")
}

func (client *Client) do(ctx context.Context, method, target string) ([]byte, error) {
	request, err := http.NewRequestWithContext(ctx, method, target, nil)
	if err != nil {
		return nil, err
	}
	ApplyDefaultHeaders(request)
	response, err := client.httpClient.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, fmt.Errorf("SmartClass HTTP %d", response.StatusCode)
	}
	return io.ReadAll(response.Body)
}

func (client *Client) FetchDomainConfig(ctx context.Context) (map[string]any, error) {
	body, err := client.do(ctx, http.MethodGet, client.baseURL+"/config.json")
	if err != nil {
		return nil, err
	}
	var response struct {
		DomainConfig string `json:"domainConfig"`
	}
	if err := json.Unmarshal(body, &response); err != nil {
		return nil, err
	}
	decrypted, err := DecryptDomainConfig(response.DomainConfig)
	if err != nil {
		return nil, err
	}
	var result map[string]any
	if err := json.Unmarshal(decrypted, &result); err != nil {
		return nil, err
	}
	return result, nil
}

func (client *Client) CSRKKey(ctx context.Context) (string, error) {
	client.csrkMu.Lock()
	defer client.csrkMu.Unlock()
	if time.Now().Before(client.csrkExpiresAt) && client.cachedCSRKKey != "" {
		return client.cachedCSRKKey, nil
	}
	values, err := client.FetchDomainConfig(ctx)
	if err != nil {
		return "", err
	}
	key, _ := values["csrkKey"].(string)
	if key == "" {
		return "", fmt.Errorf("CSRK key not found in domain config")
	}
	client.cachedCSRKKey = key
	client.csrkExpiresAt = time.Now().Add(30 * time.Minute)
	return key, nil
}

func (client *Client) CSRKToken(ctx context.Context) (string, error) {
	key, err := client.CSRKKey(ctx)
	if err != nil {
		return "", err
	}
	var token []byte
	for _, digit := range strconv.FormatInt(time.Now().UnixMilli(), 10) {
		index := int(digit - '0')
		if index < 0 || index >= len(key) {
			return "", fmt.Errorf("invalid CSRK key")
		}
		token = append(token, key[index])
	}
	return string(token), nil
}

func (client *Client) SearchVideos(ctx context.Context, condition domain.VideoSearchCondition) (domain.VideoSearchResult, error) {
	token, err := client.CSRKToken(ctx)
	if err != nil {
		return domain.VideoSearchResult{}, err
	}
	values := url.Values{
		"csrkToken": {token}, "Sort": {condition.Sort}, "Order": {strconv.Itoa(condition.Order)},
		"PageSize": {strconv.Itoa(condition.PageSize)}, "PageNumber": {strconv.Itoa(condition.PageNumber)},
		"StartDate": {condition.StartDate}, "EndDate": {condition.EndDate}, "TitleKey": {condition.TitleKey},
	}
	body, err := client.do(ctx, http.MethodGet, client.baseURL+"/Webapi/V1/Video/GetMyVideoList?"+values.Encode())
	if err != nil {
		return domain.VideoSearchResult{}, err
	}
	return parseSearchResponse(body)
}

func (client *Client) VideoInfo(ctx context.Context, id string) (domain.VideoInfo, error) {
	token, err := client.CSRKToken(ctx)
	if err != nil {
		return domain.VideoInfo{}, err
	}
	values := url.Values{"csrkToken": {token}, "NewId": {id}}
	body, err := client.do(ctx, http.MethodGet, client.baseURL+"/Video/GetVideoInfoDtoByID?"+values.Encode())
	if err != nil {
		return domain.VideoInfo{}, err
	}
	return parseVideoInfoResponse(body)
}
