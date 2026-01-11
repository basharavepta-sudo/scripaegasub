script_name = "AI Subtitle Assistant"
script_description = "AI-powered subtitle editing with ALMA/Ollama"
script_author = "AI Assistant"
script_version = "3.0"

-- Настройка путей
local script_path = debug.getinfo(1).source:match("@?(.*[\\/])")
if not script_path then
    script_path = aegisub.decode_path("?user/automation/autoload/")
end

local separator = package.config:sub(1,1)
package.path = package.path .. ";" .. script_path .. "modules" .. separator .. "?.lua"

local json = require("json")

-- Пути к файлам
local temp_dir = os.getenv("TEMP") or os.getenv("TMP") or "/tmp"
local request_file = temp_dir .. separator .. "aegisub_ai_request.json"
local response_file = temp_dir .. separator .. "aegisub_ai_response.json"
local python_script = script_path .. "alma_backend.py"
local config_file = script_path .. "config.json"
local settings_file = script_path .. "user_settings.json"

-- Кэш настроек сессии
local session_settings = nil

-- ============== Утилиты ==============

local function get_python_cmd()
    if separator == "/" then
        local handle = io.popen("which python3 2>/dev/null")
        if handle then
            local result = handle:read("*a")
            handle:close()
            if result and result ~= "" then
                return "python3"
            end
        end
        return "python"
    else
        return "python"
    end
end

local python_executable = get_python_cmd()

local function read_json_file(filepath)
    local file = io.open(filepath, "r")
    if not file then return nil end
    local content = file:read("*all")
    file:close()
    if not content or content == "" then return nil end
    local ok, data = pcall(json.decode, content)
    return ok and data or nil
end

local function write_json_file(filepath, data)
    local file = io.open(filepath, "w")
    if not file then return false end
    local ok, encoded = pcall(json.encode, data)
    if not ok then file:close() return false end
    file:write(encoded)
    file:close()
    return true
end

-- ============== Настройки ==============

local function load_settings()
    if session_settings then return session_settings end

    -- Загружаем из файла или создаём дефолтные
    session_settings = read_json_file(settings_file) or {
        global_context = "",
        default_instructions = "",
        translation_style = "natural",
        num_variants = 3,
        context_lines = 2,
        source_path = "",  -- Путь к английским субтитрам
        match_length = true,  -- Соблюдать длину оригинала
        model = "HY-MT1.5-7B-FP8"  -- Модель Ollama
    }
    return session_settings
end

local function save_settings()
    if session_settings then
        write_json_file(settings_file, session_settings)

        -- Также обновляем config.json
        local config = read_json_file(config_file) or {}
        config.global_context = session_settings.global_context
        config.default_instructions = session_settings.default_instructions
        config.translation_style = session_settings.translation_style
        config.num_variants = session_settings.num_variants
        config.model = session_settings.model
        write_json_file(config_file, config)
    end
end

-- ============== UI Настроек проекта ==============

local function show_project_settings()
    local settings = load_settings()

    local style_items = {"natural", "formal", "casual", "literal"}
    local model_items = {"HY-MT1.5-7B-FP8", "llama3", "mistral", "qwen2", "gemma2"}

    local dialog = {
        {class="label", label="=== Настройки проекта ===", x=0, y=0, width=4},

        {class="label", label="Модель Ollama:", x=0, y=1},
        {class="dropdown", name="model", items=model_items, value=settings.model or "HY-MT1.5-7B-FP8", x=1, y=1, width=3},

        {class="label", label="Глобальный контекст (описание фильма/сериала):", x=0, y=2, width=4},
        {class="textbox", name="global_context", value=settings.global_context or "", x=0, y=3, width=4, height=2},

        {class="label", label="Инструкции по умолчанию:", x=0, y=5, width=4},
        {class="edit", name="default_instructions", value=settings.default_instructions or "", x=0, y=6, width=4},

        {class="label", label="Путь к английским субтитрам (.srt/.txt):", x=0, y=7, width=4},
        {class="edit", name="source_path", value=settings.source_path or "", x=0, y=8, width=4},

        {class="label", label="Стиль:", x=0, y=9},
        {class="dropdown", name="style", items=style_items, value=settings.translation_style or "natural", x=1, y=9, width=1},

        {class="label", label="Вариантов:", x=2, y=9},
        {class="intedit", name="num_variants", value=settings.num_variants or 3, min=1, max=5, x=3, y=9},

        {class="label", label="Строк контекста:", x=0, y=10},
        {class="intedit", name="context_lines", value=settings.context_lines or 2, min=0, max=5, x=1, y=10},

        {class="checkbox", name="match_length", label="Соблюдать длину оригинала (±10%)", value=settings.match_length ~= false, x=2, y=10, width=2},
    }

    local buttons = {"Сохранить", "Выбрать файл EN", "Отмена"}
    local button, results = aegisub.dialog.display(dialog, buttons)

    if button == "Выбрать файл EN" then
        -- Показываем диалог выбора файла
        local path = aegisub.dialog.open("Выберите английские субтитры", "", "", "Субтитры (*.srt;*.txt)|*.srt;*.txt", false, true)
        if path then
            settings.source_path = path
            save_settings()
            aegisub.log("Путь сохранён: " .. path .. "\n")
        end
        return show_project_settings()  -- Показываем настройки снова
    elseif button == "Сохранить" then
        session_settings.global_context = results.global_context
        session_settings.default_instructions = results.default_instructions
        session_settings.translation_style = results.style
        session_settings.num_variants = results.num_variants
        session_settings.context_lines = results.context_lines
        session_settings.source_path = results.source_path
        session_settings.match_length = results.match_length
        session_settings.model = results.model
        save_settings()
        aegisub.log("Настройки сохранены! Модель: " .. results.model .. "\n")
    end
end

-- ============== Парсинг SRT ==============

local function parse_srt_file(filepath)
    local file = io.open(filepath, "r")
    if not file then return nil end

    local content = file:read("*all")
    file:close()

    local blocks = {}
    local current_block = {}
    local block_num = nil

    for line in content:gmatch("[^\r\n]+") do
        if line:find("^\239\187\191") then
            line = line:sub(4)
        end
        if line:match("^%d+$") then
            if block_num and current_block.text then
                blocks[block_num] = current_block.text
            end
            block_num = tonumber(line)
            current_block = {text = ""}
        elseif line:match("^%d%d:%d%d:%d%d") then
            -- таймкод
        elseif line ~= "" and block_num then
            if current_block.text and current_block.text ~= "" then
                current_block.text = current_block.text .. "\n"
            end
            current_block.text = (current_block.text or "") .. line
        end
    end
    if block_num and current_block.text then
        blocks[block_num] = current_block.text
    end

    return blocks
end

-- ============== Запуск Python ==============

local function run_python_backend()
    local cmd
    if separator == "/" then
        -- Linux/Mac: просто запускаем
        cmd = python_executable .. ' "' .. python_script .. '" "' .. request_file .. '" "' .. response_file .. '" "' .. config_file .. '" 2>&1'
    else
        -- Windows: используем start /b /wait чтобы скрыть окно CMD
        -- Альтернативно используем pythonw если доступен
        cmd = 'start /b /wait "" ' .. python_executable .. ' "' .. python_script .. '" "' .. request_file .. '" "' .. response_file .. '" "' .. config_file .. '"'
    end

    -- Используем io.popen вместо os.execute для скрытия окна
    local handle = io.popen(cmd .. " && echo __SUCCESS__ || echo __FAILED__", "r")
    if handle then
        local output = handle:read("*a")
        handle:close()
        -- Проверяем успех по маркеру или наличию response файла
        if output:find("__SUCCESS__") then
            return true
        end
    end

    -- Fallback: проверяем наличие response файла
    local f = io.open(response_file, "r")
    if f then
        local content = f:read("*a")
        f:close()
        return content and content ~= ""
    end
    return false
end

-- ============== Главная функция перевода ==============

local function translate_line(subs, sel, active)
    local settings = load_settings()
    local line = subs[active]

    if line.class ~= "dialogue" then
        aegisub.dialog.display({{class="label", label="Выберите строку диалога!"}}, {"OK"})
        return
    end

    -- Короткий контекст для отображения
    local ctx_preview = settings.global_context or ""
    if #ctx_preview > 40 then
        ctx_preview = ctx_preview:sub(1, 40) .. "..."
    end
    if ctx_preview == "" then
        ctx_preview = "(не задан - нажми Настройки)"
    end

    -- Диалог быстрых настроек
    local quick_dialog = {
        {class="label", label="Текущая строка: " .. (line.text:sub(1, 50) .. (#line.text > 50 and "..." or "")), x=0, y=0, width=4},
        {class="label", label="", x=0, y=1},

        -- Быстрое отображение/редактирование контекста
        {class="label", label="Контекст проекта:", x=0, y=2},
        {class="edit", name="global_context", value=settings.global_context or "", x=0, y=3, width=4},

        {class="label", label="Инструкции для этого перевода:", x=0, y=4, width=4},
        {class="edit", name="instructions", value="", x=0, y=5, width=4},

        {class="label", label="Вариантов:", x=0, y=6},
        {class="intedit", name="num_variants", value=settings.num_variants, min=1, max=5, x=1, y=6},

        {class="label", label="Стиль:", x=2, y=6},
        {class="dropdown", name="style", items={"natural", "formal", "casual", "literal"}, value=settings.translation_style or "natural", x=3, y=6},

        {class="checkbox", name="use_context", label="Показать AI соседние субтитры (контекст до/после)", value=true, x=0, y=7, width=4},
    }

    local btn, res = aegisub.dialog.display(quick_dialog, {"Перевести", "Настройки", "Отмена"})

    if btn == "Настройки" then
        show_project_settings()
        return translate_line(subs, sel, active)  -- Повторить после настройки
    elseif btn == "Отмена" then
        return
    end

    -- Сохраняем изменённый контекст и стиль
    if res.global_context ~= session_settings.global_context or res.style ~= session_settings.translation_style then
        session_settings.global_context = res.global_context
        session_settings.translation_style = res.style
        save_settings()
    end

    -- Загружаем английские субтитры
    local source_blocks = nil
    local source_path = settings.source_path or ""

    -- Сначала пробуем указанный путь
    if source_path ~= "" then
        local f = io.open(source_path, "r")
        if f then
            f:close()
            source_blocks = parse_srt_file(source_path)
            aegisub.log("Загружены EN субтитры: " .. source_path .. "\n")
        else
            aegisub.log("Не найден файл EN субтитров: " .. source_path .. "\n")
        end
    end

    -- Если не указан путь, пробуем автопоиск
    if not source_blocks then
        local sub_path = aegisub.decode_path("?script")
        local sub_name = aegisub.file_name()
        if sub_name then
            sub_name = sub_name:match("(.+)%..+$") or sub_name
        else
            sub_name = "unknown"
        end

        for _, ext in ipairs({"_en.srt", "_en.txt", ".en.srt", ".srt", ".txt"}) do
            local try_path = sub_path .. separator .. sub_name .. ext
            local f = io.open(try_path, "r")
            if f then
                f:close()
                source_blocks = parse_srt_file(try_path)
                if source_blocks then
                    aegisub.log("Автонайден EN файл: " .. try_path .. "\n")
                    break
                end
            end
        end
    end

    -- Строим маппинг диалогов
    local line_id_map = {}
    local dialogue_count = 0
    for i = 1, #subs do
        if subs[i].class == "dialogue" then
            dialogue_count = dialogue_count + 1
            line_id_map[i] = dialogue_count
        end
    end

    local current_d_idx = line_id_map[active]

    -- Собираем контекст (только если галочка включена)
    local context_before = {}
    local context_after = {}

    if res.use_context then
        local ctx_size = settings.context_lines or 2

        for i = math.max(1, active - ctx_size), active - 1 do
            if subs[i].class == "dialogue" then
                local d_idx = line_id_map[i]
                table.insert(context_before, {
                    ru = subs[i].text,
                    en = source_blocks and d_idx and source_blocks[d_idx] or ""
                })
            end
        end

        for i = active + 1, math.min(#subs, active + ctx_size) do
            if subs[i].class == "dialogue" then
                local d_idx = line_id_map[i]
                table.insert(context_after, {
                    ru = subs[i].text,
                    en = source_blocks and d_idx and source_blocks[d_idx] or ""
                })
            end
        end
    end

    -- Считаем длину оригинала (без \N)
    local original_text = line.text:gsub("\\N", " "):gsub("%s+", " ")
    local original_length = #original_text

    -- Формируем запрос
    local request_data = {
        current_line = {
            ru = line.text,
            en = source_blocks and source_blocks[current_d_idx] or "",
            duration = (line.end_time - line.start_time) / 1000.0,
            max_chars = settings.match_length and original_length or 0  -- 0 = без ограничения
        },
        context_before = context_before,
        context_after = context_after,
        feedback = res.instructions  -- Инструкции пользователя
    }

    write_json_file(request_file, request_data)

    -- Обновляем настройки в config
    local config = read_json_file(config_file) or {}
    config.num_variants = res.num_variants
    config.global_context = res.global_context
    config.translation_style = res.style
    write_json_file(config_file, config)

    -- Запускаем
    aegisub.progress.task("AI думает...")
    local success = run_python_backend()

    if not success then
        aegisub.dialog.display({{class="label", label="Ошибка Python! Проверьте Ollama."}}, {"OK"})
        return
    end

    local response = read_json_file(response_file)
    if not response then
        aegisub.dialog.display({{class="label", label="Нет ответа от AI."}}, {"OK"})
        return
    end

    if response.error then
        aegisub.dialog.display({{class="label", label="Ошибка: " .. response.error}}, {"OK"})
        return
    end

    if not response.variants or #response.variants == 0 then
        aegisub.dialog.display({{class="label", label="AI не вернул вариантов."}}, {"OK"})
        return
    end

    -- Показываем результаты
    show_result_dialog(subs, active, line, response.variants, request_data, source_blocks, res.num_variants)
end

-- ============== Форматирование строк ==============

local function format_line_breaks(text)
    -- Разбивает длинную строку на 2 части для читаемости
    -- Ставит \N примерно посередине, желательно после пунктуации

    -- Убираем лишние пробелы и существующие \N
    text = text:gsub("\\N", " "):gsub("%s+", " "):gsub("^%s+", ""):gsub("%s+$", "")

    -- Если строка короткая (меньше 45 символов) - не трогаем
    if #text <= 45 then
        return text
    end

    -- Если уже есть перенос - не трогаем
    if text:find("\\N") then
        return text
    end

    -- Ищем лучшее место для разрыва около середины
    local mid = math.floor(#text / 2)
    local best_pos = nil
    local best_score = 999

    -- Ищем пунктуацию около середины (. ! ? ,)
    for i = math.max(10, mid - 20), math.min(#text - 10, mid + 20) do
        local char = text:sub(i, i)
        local next_char = text:sub(i + 1, i + 1)

        if next_char == " " then
            local score = math.abs(i - mid)

            -- Предпочитаем конец предложения
            if char == "." or char == "!" or char == "?" then
                score = score - 15  -- Сильно предпочитаем
            elseif char == "," then
                score = score - 5   -- Немного предпочитаем
            end

            if score < best_score then
                best_score = score
                best_pos = i + 1  -- После пробела
            end
        end
    end

    -- Если не нашли пунктуацию, ищем просто пробел около середины
    if not best_pos then
        for i = mid, mid + 15 do
            if text:sub(i, i) == " " then
                best_pos = i
                break
            end
        end
        if not best_pos then
            for i = mid, mid - 15, -1 do
                if text:sub(i, i) == " " then
                    best_pos = i
                    break
                end
            end
        end
    end

    -- Вставляем \N
    if best_pos and best_pos > 10 and best_pos < #text - 10 then
        local part1 = text:sub(1, best_pos - 1):gsub("%s+$", "")
        local part2 = text:sub(best_pos + 1):gsub("^%s+", "")
        return part1 .. " \\N" .. part2
    end

    return text
end

-- ============== Диалог результатов ==============

function show_result_dialog(subs, active, line, variants, request_data, source_blocks, num_variants)
    local truncate = function(text, max)
        max = max or 60
        return #text > max and text:sub(1, max) .. "..." or text
    end

    local dialog = {
        {class="label", label="Оригинал: " .. truncate(line.text), x=0, y=0, width=4},
        {class="label", label="", x=0, y=1},
        {class="label", label="Варианты (редактируйте любой):", x=0, y=2, width=4},
    }

    -- Показываем каждый вариант как отдельное редактируемое поле
    local y_pos = 3
    for i, v in ipairs(variants) do
        table.insert(dialog, {class="checkbox", name="use_" .. i, label=i .. ".", value=(i == 1), x=0, y=y_pos})
        table.insert(dialog, {class="edit", name="var_" .. i, value=v, x=1, y=y_pos, width=3})
        y_pos = y_pos + 1
    end

    y_pos = y_pos + 1
    table.insert(dialog, {class="checkbox", name="auto_format", label="Авто-форматирование (\\N после пунктуации и каждые 6 слов)", value=true, x=0, y=y_pos, width=4})

    y_pos = y_pos + 1
    table.insert(dialog, {class="label", label="Feedback для retry:", x=0, y=y_pos, width=4})
    y_pos = y_pos + 1
    table.insert(dialog, {class="edit", name="feedback", value="", x=0, y=y_pos, width=4})

    local buttons = {"Применить", "Retry", "Отмена"}
    local btn, res = aegisub.dialog.display(dialog, buttons)

    if btn == "Отмена" then
        return
    elseif btn == "Retry" then
        request_data.feedback = res.feedback ~= "" and res.feedback or "дай другие варианты"
        write_json_file(request_file, request_data)

        aegisub.progress.task("AI думает снова...")
        if run_python_backend() then
            local new_response = read_json_file(response_file)
            if new_response and new_response.variants then
                show_result_dialog(subs, active, line, new_response.variants, request_data, source_blocks, num_variants)
            end
        end
        return
    else
        -- Применить - находим выбранный вариант
        local final = nil
        for i = 1, #variants do
            if res["use_" .. i] then
                final = res["var_" .. i]
                break
            end
        end

        -- Если ничего не выбрано, берём первый
        if not final then
            final = res["var_1"] or variants[1]
        end

        -- Применяем авто-форматирование если включено
        if res.auto_format then
            final = format_line_breaks(final)
        end

        line.text = final
        subs[active] = line
        aegisub.set_undo_point("AI Subtitle Edit")
    end
end

-- ============== Batch перевод ==============

local function translate_batch(subs, sel, active)
    if #sel < 2 then
        aegisub.dialog.display({{class="label", label="Выделите 2+ строки для batch перевода!"}}, {"OK"})
        return
    end

    local settings = load_settings()

    -- Подтверждение
    local confirm = aegisub.dialog.display({
        {class="label", label="Batch перевод " .. #sel .. " строк", x=0, y=0},
        {class="label", label="Это может занять время.", x=0, y=1},
    }, {"Начать", "Отмена"})

    if confirm ~= "Начать" then return end

    -- Собираем строки
    local batch_lines = {}
    for _, i in ipairs(sel) do
        if subs[i].class == "dialogue" then
            table.insert(batch_lines, {
                idx = i,
                en = subs[i].text,
                ru = subs[i].text
            })
        end
    end

    local request_data = {
        batch_lines = batch_lines
    }

    write_json_file(request_file, request_data)

    aegisub.progress.task("AI обрабатывает " .. #batch_lines .. " строк...")
    local success = run_python_backend()

    if not success then
        aegisub.dialog.display({{class="label", label="Ошибка batch перевода!"}}, {"OK"})
        return
    end

    local response = read_json_file(response_file)
    if not response or not response.batch_variants then
        aegisub.dialog.display({{class="label", label="Нет результатов batch."}}, {"OK"})
        return
    end

    -- Применяем первый вариант к каждой строке
    local applied = 0
    for i, line_data in ipairs(batch_lines) do
        local variants = response.batch_variants[i]
        if variants and #variants > 0 then
            local line = subs[line_data.idx]
            line.text = variants[1]
            subs[line_data.idx] = line
            applied = applied + 1
        end
    end

    aegisub.set_undo_point("AI Batch Translate")
    aegisub.dialog.display({{class="label", label="Применено: " .. applied .. " из " .. #batch_lines}}, {"OK"})
end

-- ============== Регистрация ==============

aegisub.register_macro(
    script_name .. "/Перевести строку",
    "Перевести текущую строку с AI",
    translate_line
)

aegisub.register_macro(
    script_name .. "/Batch перевод (выделенные)",
    "Перевести несколько выделенных строк",
    translate_batch
)

aegisub.register_macro(
    script_name .. "/Настройки проекта",
    "Настроить глобальный контекст, стиль, инструкции",
    show_project_settings
)
