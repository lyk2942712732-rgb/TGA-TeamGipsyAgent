# Historical Session migration note

The earlier schema-v5 Session write and execution contracts are retired. The current
application accepts only schema v6.

Use the explicit offline migration workflow:

```powershell
tga migrate --db runs\<task-id>\evidence.db --backup --dry-run
tga migrate --db runs\<task-id>\evidence.db --apply
tga migrate --db runs\<task-id>\evidence.db --verify
```

There is no additive runtime upgrade, online read fallback, old UI projection, or dual write.
