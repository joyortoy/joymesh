# Authentication

Connector definitions describe browser OAuth, device code, vendor login, environment keys,
provider configuration, subscriptions, and cloud credential chains. File existence is not proof
of authentication.

Tokens remain on the node. Browser responses contain status and instructions, never token
contents or unnecessary credential paths. Subscription login and separately billed API access
are distinct provider modes and cannot be silently exchanged.
