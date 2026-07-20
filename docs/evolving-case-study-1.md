The original platform was a custom-build platform called grant tracker which had
been developed to manage and track grant applications and funding, contacts,
organisations, and extreme complexity in some cases.

Lots of additional process lived outside of that platform like regranting,
contracts, and reporting.

The technology choice came first. Salesforce which is a widely used customer
relationship management platform.

A vendor was chose to implement on the saleforce platform, but based on a naive
understanding of the application lifecycle and complexity of the portfolio.

- Customers in the platform were essentially researchers who applicants.
- Salesforce did not support a single customer belonging to multiple
  organisations, or keeping referential integrity of their moves between
  organisations over time.
- Salesforces document management systems was not suitable for the complexity,
  and scale of documents which needed to be managed, so third-party tools needed
  to be integrated at higher cost.
- The Salesforce engine itself is not a business process engine, so the vendor
  implemented the grant application process as a series of forms and workflows
  which centered around a single salesforce god object, as more and more processes
  and information were added, the complexity of the system increased and the platform
  became increasingly brittle and costly in time and money to maintain.
- The SF object model was not suitable for recording the state of the
  application process over time. With no transition model, the system was unable
  to support complex workflows and current workflow stage was inferred from
  combinations of field values, which made it difficult to understand the current
  state of the application or historic moves through states.
- As time goes by, cost of maintaining the system has increased, and extending
  it comes with longer delays and non-lineage development costs.
