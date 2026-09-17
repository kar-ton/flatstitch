# Як опублікувати flatstitch на GitHub

Покрокова інструкція. Усі команди виконуються у теці `flatstitch`
(розпакований архів), якщо не вказано інше.

---

## 0. Що вже підготовлено

В архіві вже лежить усе, що потрібно для нормального публічного
репозиторію, окремо створювати не треба:

- `README.md` (англійською — основна) + `README.uk.md` (українською,
  з взаємними посиланнями між ними)
- `LICENSE` — MIT
- `CONTRIBUTING.md` — як додати переклад / структура коду
- `.gitignore` — уже виключає `__pycache__`, `*.deb`, `*.tiff`,
  скриншоти, тимчасові файли редакторів

**Перед публікацією замініть у двох місцях заглушку автора на свої дані:**

```bash
grep -rn "you@example.com" .
```

Знайде `LICENSE` (рядок Copyright), `deb/.../DEBIAN/control` (Maintainer)
і `deb/.../usr/share/doc/flatstitch/copyright`. Вкажіть там своє ім'я та
e-mail (або GitHub-адресу вигляду `nickname@users.noreply.github.com`,
якщо не хочете світити справжню пошту).

---

## 1. Одноразове налаштування Git (якщо ще не робили)

```bash
git config --global user.name "Ваше Ім'я"
git config --global user.email "ваша@пошта"
```

Через ваш проксі Git теж треба налаштувати окремо, інакше `push` просто
зависне:

```bash
git config --global http.proxy http://10.0.4.70:3128
git config --global https.proxy http://10.0.4.70:3128
```

(Якщо проксі згодом зникне: `git config --global --unset http.proxy` і
те саме для `https.proxy`.)

---

## 2. Створити репозиторій на GitHub

У браузері: **github.com → + (вгорі праворуч) → New repository**

- **Repository name**: `flatstitch`
- **Description**: `Stitch flat scanned images into one seamless TIFF (a Microsoft ICE alternative for Linux)`
- **Public**
- **Не** ставте галочки «Add a README», «Add .gitignore», «Choose a
  license» — усі три файли вже є в проєкті, інакше при першому `push`
  виникне конфлікт, який доведеться розв'язувати вручну.

Натисніть **Create repository**. Сторінку, що відкриється, не закривайте —
звідти знадобиться URL.

---

## 3. Локальний репозиторій і перший коміт

```bash
cd /шлях/до/flatstitch

git init
git branch -M main
git add .
git status          # ПЕРЕВІРТЕ цей вивід перед комітом (див. нижче)
```

У `git status` **не повинно** бути:
- `__pycache__/`, `*.pyc`
- `*.deb` (готовий пакет публікується як Release — крок 6, а не як файл у
  репозиторії; це те, для чого `.gitignore` його й виключає)
- ваших тестових сканів чи `.tiff`-результатів

Якщо все чисто:

```bash
git commit -m "flatstitch 1.0.0: rigid-transform scan stitcher, CLI + GUI, 10 languages"
```

---

## 4. Прив'язати GitHub і надіслати код

Візьміть URL зі сторінки створеного репозиторію. Через HTTPS:

```bash
git remote add origin https://github.com/ВАШ_НІК/flatstitch.git
git push -u origin main
```

GitHub попросить логін і пароль — **у полі пароля звичайний пароль не
підійде**, потрібен Personal Access Token:

**github.com → Settings (у меню профілю) → Developer settings → Personal
access tokens → Tokens (classic) → Generate new token (classic)**
→ поставте галочку `repo` → Generate → **скопіюйте токен одразу**
(вдруге його не покажуть) → вставте замість пароля.

Щоб не вводити токен щоразу:

```bash
git config --global credential.helper store
```

(Зберігає його у відкритому вигляді в `~/.git-credentials` — прийнятно
для домашньої машини; на спільному комп'ютері краще
`credential.helper cache`.)

### Альтернатива: SSH замість токена

Якщо плануєте працювати з GitHub регулярно, SSH зручніше:

```bash
ssh-keygen -t ed25519 -C "ваша@пошта"     # Enter на всі запити
cat ~/.ssh/id_ed25519.pub                 # скопіюйте весь рядок
```

Вставте його на **github.com → Settings → SSH and GPG keys → New SSH
key**. Тоді:

```bash
git remote set-url origin git@github.com:ВАШ_НІК/flatstitch.git
git push -u origin main
```

(Зауважте: SSH через HTTP-проксі не працює напряму — якщо проксі
обов'язковий, залишайтеся на HTTPS+токені.)

---

## 5. Оформити сторінку репозиторію

Після `push` на сторінці репозиторію:

1. Праворуч від назви — шестерня біля **About** → у **Topics** додайте:
   `image-stitching`, `scanner`, `tiff`, `opencv`, `python`, `linux`,
   `panorama`, `sift`, `ransac`, `tkinter`. Це головне джерело
   випадкових відвідувачів.
2. Там же увімкніть галочку **Releases**, щоб пакет із кроку 6 було
   видно на головній.
3. Додайте скриншот у README — він робить більше для першого враження,
   ніж будь-який текст. Запустіть GUI, зробіть знімок вікна, покладіть
   його в теку `docs/` і додайте рядок у `README.md`:

   ```markdown
   ![flatstitch GUI](docs/screenshot.png)
   ```

   Потім: `git add docs/screenshot.png README.md && git commit -m "Add
   screenshot" && git push`.

---

## 6. Опублікувати .deb як Release

Готовий пакет не слід тримати у самому репозиторії (це бінарний файл,
який роздуває історію Git) — для цього є Releases:

**Сторінка репозиторію → Releases (праворуч) → Create a new release**

- **Choose a tag** → введіть `v1.0.0` → *Create new tag on publish*
- **Release title**: `flatstitch 1.0.0`
- **Describe this release** — коротко, наприклад:

  ```
  Перший випуск.

  - Жорстке перетворення (поворот+зсув) замість гомографії — не
    деформує прямі лінії й текст на сканах
  - CLI + GUI (Tkinter), 10 мов інтерфейсу
  - Глобальне уточнення розташування (bundle adjustment) проти
    накопичення похибки на довгих ланцюжках сканів
  - Потокове компонування: пам'ять залежить від розміру полотна, а не
    від кількості сканів
  - Вивід у TIFF (LZW, без втрат; BigTIFF для великих результатів)

  Встановлення:
      sudo apt install ./flatstitch_1.0.0-1_all.deb
  ```

- **Attach binaries** — перетягніть у це поле файл
  `flatstitch_1.0.0-1_all.deb`
- **Publish release**

---

## 7. Подальші зміни

```bash
git add -A
git commit -m "Короткий опис що змінилось"
git push
```

Для нової версії: підніміть версію в `deb/.../DEBIAN/control`
(`Version:`) і в `flatstitch/__init__.py` (`__version__`), додайте запис
у `deb/.../usr/share/doc/flatstitch/changelog.Debian.gz`, перезберіть
пакет (див. розділ «Building the .deb from source» у README) і створіть
новий Release із тегом `v1.0.1`.

> Збірку пакета з нуля перевірено саме цим шляхом: свіжий `git clone` →
> `fakeroot dpkg-deb --build` → `apt install ./...deb` → робочий
> `flatstitch`. Файл `DEBIAN/md5sums` навмисно не в репозиторії (він
> генерований), і на збірку це не впливає.

---

## Якщо щось не вдається

- **`push` зависає без помилки** — майже завжди проксі (крок 1).
  Перевірте: `git config --global --get http.proxy`
- **`Authentication failed`** — у полі пароля потрібен Personal Access
  Token, а не пароль від акаунта (крок 4).
- **`Updates were rejected because the remote contains work that you do
  not have locally`** — під час створення репозиторію все ж додався
  README/LICENSE з боку GitHub. Найпростіше:
  `git pull --rebase origin main`, розв'язати можливий конфлікт у
  README/LICENSE, тоді `git push`.
- **Випадково закомітили щось зайве** (тестові скани, `.deb`, токен) —
  якщо ще не робили `push`: `git reset HEAD~1` (коміт скасується, файли
  залишаться на диску), додайте потрібне у `.gitignore` і закомітьте
  заново. Якщо вже надіслали на GitHub, а це був **секрет** (токен,
  пароль) — вважайте його скомпрометованим: відкликайте його в
  налаштуваннях GitHub і створюйте новий, самого лише видалення файлу
  недостатньо, бо він залишається в історії Git.
