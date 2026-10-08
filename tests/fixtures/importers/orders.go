package shop

// Order mirrors shop.v1.Order for the JSON gateway.
type Order struct {
	ID         string            `json:"id"`
	Items      []string          `json:"items"`
	TotalCents int64             `json:"total_cents"`
	Status     string            `json:"status"`
	Labels     map[string]string `json:"labels"`
	CardToken  string            `json:"card_token,omitempty"`
	InvoiceID  string            `json:"invoice_id,omitempty"`
}

// Currency codes accepted by the billing service.
const (
	CurrencyEUR = "EUR"
	CurrencyUSD = "USD"
)
