package main

import (
	"net/http"

	"github.com/labstack/echo/v4"
)

func main() {
	e := echo.New()
	e.GET("/", h)
	g := e.Group("/admin", mw)
	g.GET("/users/:id", h)
	g.GET("", h)
	e.Add("DELETE", "/things/:id", h)
	e.Match([]string{"PUT", http.MethodPatch}, "/things/:id", h)
	e.Static("/static", "assets")
	e.File("/favicon.ico", "favicon.ico")
	e.CONNECT("/tunnel", h)
	api := e.Group("/api")
	v := api.Group("/v1")
	v.POST("/orders", h)
	http.Handle("/std", nil)
}

func mount(g *echo.Group) {
	g.PUT("/x", h)
}
