## Token Güvenliği Adımları

1. Öncelikle kodunuzu düzenleyin:

```python
import os
from dotenv import load_dotenv

# .env dosyasını yükle (yerel geliştirme için)
load_dotenv()

# Diğer kodlar...

# Botu çalıştır
bot.run(os.getenv('DISCORD_TOKEN'))
```

2. Projenizde `.env` dosyası oluşturun (SADECE yerel geliştirme için):
```
DISCORD_TOKEN=your_token_here
```

3. `.gitignore` dosyası oluşturun ve içine şunları ekleyin:
```
.env
__pycache__/
*.py[cod]
*$py.class
```

Bu şekilde token'ınız GitHub'a yüklenmeyecek, Railway'de ise çevre değişkeni olarak ekleyeceksiniz.