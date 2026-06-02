package smartclass

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"time"
)

type SSOClient struct {
	httpClient *http.Client
	baseURL    string
}

func NewSSOClient(httpClient *http.Client) *SSOClient {
	return &SSOClient{httpClient: httpClient, baseURL: "https://i.njupt.edu.cn"}
}

func (client *SSOClient) Login(ctx context.Context, username, password string) error {
	checkKey := strconv.FormatInt(time.Now().UnixMilli(), 10)
	encryptedUsername, err := EncryptSSOField(username, checkKey)
	if err != nil {
		return err
	}
	encryptedPassword, err := EncryptSSOField(password, checkKey)
	if err != nil {
		return err
	}
	payload := map[string]any{
		"username": encryptedUsername, "password": encryptedPassword,
		"captchaVerification": nil, "checkKey": checkKey, "appId": "common", "mode": "none",
	}
	body, _ := json.Marshal(payload)
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, client.baseURL+"/ssoLogin/login", bytes.NewReader(body))
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := client.httpClient.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	var result struct {
		Success bool   `json:"success"`
		Code    int    `json:"code"`
		Message string `json:"message"`
	}
	if err := json.NewDecoder(response.Body).Decode(&result); err != nil {
		return err
	}
	if !result.Success {
		return fmt.Errorf("SSO 登录失败 (%d): %s", result.Code, result.Message)
	}
	return nil
}

func (client *SSOClient) GrantService(ctx context.Context, service string) error {
	target := client.baseURL + "/cas/login?service=" + url.QueryEscape(service)
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
	if err != nil {
		return err
	}
	response, err := client.httpClient.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 400 {
		return fmt.Errorf("grant service failed: HTTP %d", response.StatusCode)
	}
	return nil
}
