package main

import "math"

// The web client shows the same total before the order is sent. The two
// implementations must round identically, or the customer sees one price and
// is charged another.

// crossweft:begin price-rounding
func totalCents(unitPrice float64, quantity int) int64 {
	return int64(math.Floor(unitPrice*float64(quantity)*100 + 0.5))
}
// crossweft:end price-rounding
