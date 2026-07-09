package activities

import (
	"context"
	"fmt"
)

type Hello struct{}

func (Hello) SayHello(ctx context.Context, name string) (string, error) {
	return fmt.Sprintf("Hello, %s!", name), nil
}
