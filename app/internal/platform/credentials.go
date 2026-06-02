package platform

import "context"

type CredentialStore interface {
	Get(ctx context.Context, service, username string) (string, bool, error)
	Set(ctx context.Context, service, username, password string) error
	Delete(ctx context.Context, service, username string) error
}

func credentialTarget(service, username string) string {
	return service + ":" + username
}
