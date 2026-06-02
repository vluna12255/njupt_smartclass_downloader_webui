//go:build !windows

package platform

import (
	"context"
	"errors"
)

type unsupportedCredentialStore struct{}

var errCredentialsUnsupported = errors.New("system credential storage is only supported on Windows")

func NewCredentialStore() CredentialStore {
	return unsupportedCredentialStore{}
}

func (unsupportedCredentialStore) Get(context.Context, string, string) (string, bool, error) {
	return "", false, errCredentialsUnsupported
}

func (unsupportedCredentialStore) Set(context.Context, string, string, string) error {
	return errCredentialsUnsupported
}

func (unsupportedCredentialStore) Delete(context.Context, string, string) error {
	return errCredentialsUnsupported
}
