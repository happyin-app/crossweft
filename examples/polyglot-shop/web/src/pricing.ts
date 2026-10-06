// Shown to the customer before the order is sent; the server charges with
// the same rounding (server/pricing.go).

// crossweft:begin price-rounding
export function totalCents(unitPrice: number, quantity: number): number {
  return Math.floor(unitPrice * quantity * 100 + 0.5);
}
// crossweft:end price-rounding
