package main

import (
	"net/http"

	"github.com/gin-gonic/gin"
)

const ordersPath = "/orders"

type server struct {
	engine *gin.Engine
}

func main() {
	r := gin.Default()
	r.GET("/health", h)
	v1 := r.Group("/v1")
	{
		v1.GET("/orders/:id", h)
		admin := v1.Group("/admin", auth)
		admin.DELETE("/users/:id", h)
		admin.Group("/deep").POST("/x", h)
	}
	r.Handle("PATCH", "/legacy", h)
	r.Handle(http.MethodPut, "/legacy", h)
	r.Match([]string{http.MethodGet, "POST"}, "/match", h)
	r.Any("/any", h)
	r.Static("/assets", "./public")
	r.StaticFile("/favicon.ico", "./favicon.ico")
	r.GET(ordersPath, h)
	dyn := r.Group(prefix)
	dyn.GET("/lost", h)
	registerV2(r.Group("/v2"))
	// r.GET("/commented", h)
	http.Handle("/std", nil)
}

func registerV2(rg *gin.RouterGroup) {
	rg.GET("/items", h)
}

func (s *server) routes() {
	s.engine.POST("/field", h)
}
