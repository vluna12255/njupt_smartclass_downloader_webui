package smartclass

import (
	"net/http"
	"net/http/cookiejar"
)

func newCookieJar() (http.CookieJar, error) {
	return cookiejar.New(nil)
}
