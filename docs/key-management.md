# Key Management

## Principles

* Private keys exist only on JoyMesh hosts.
* JoyCLI stores public keys only.
* Never silently generate production signing keys.
* Rotation uses overlapping active keys.

## Commands

```bash
joymesh runtime key generate --destination /etc/joymesh/keys/runtime.key --key-id prod-2
joymesh runtime key inspect --path /etc/joymesh/keys/runtime.key

joyctl runtime publisher-key add --key-id prod-2 --public-key '...' 
joyctl runtime publisher-key rotate --new-key-id prod-2 --public-key '...' --old-key-id prod-1
# After publisher switches and rollback window ends:
joyctl runtime publisher-key disable prod-1 --reason rollback-window-complete
joyctl runtime publisher-key revoke prod-1 --reason retired
joyctl runtime publisher-key list
```

## Rotation sequence

1. old key active
2. new key active (overlap)
3. publisher switches to new key
4. new signed publication verified
5. old key disabled
6. old key revoked after rollback window
