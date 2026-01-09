script_name = "AI Subtitle Assistant"
script_description = "AI-powered subtitle editing with Gemini"
script_author = "AI Assistant"
script_version = "1.0"

-- Настройка путей для модулей
-- Получаем путь к текущему скрипту, чтобы найти соседние файлы
local script_path = debug.getinfo(1).source:match("@?(.*[\\/])")
if not script_path then
    -- Fallback если не удалось определить путь (редко)
    script_path = aegisub.decode_path("?user/automation/autoload/")
end

local separator = package.config:sub(1,1)
package.path = package.path .. ";" .. script_path .. "modules" .. separator .. "?.lua"

local json = require("json")

-- Путь к временным файлам
local temp_dir = os.getenv("TEMP") or os.getenv("TMP") or "/tmp"
local request_file = temp_dir .. separator .. "aegisub_ai_request.json"
local response_file = temp_dir .. separator .. "aegisub_ai_response.json"

-- Пути к скриптам и конфигам
local python_script = script_path .. "gemini_backend.py"
local config_file = script_path .. "config.json"

-- Парсинг SRT файла (для исходника)
function parse_srt_file(filepath)
    local file = io.open(filepath, "r")
    if not file then
        return nil, "Cannot open source file"
    end

    local content = file:read("*all")
    file:close()

    local blocks = {}
    local current_block = {}
    local block_num = nil

    for line in content:gmatch("[^\r\n]+") do
        -- Удаляем BOM если есть
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
            -- Таймкод, пропускаем
        elseif line ~= "" and block_num then
            if current_block.text and current_block.text ~= "" then
                current_block.text = current_block.text .. "\n"
            end
            current_block.text = (current_block.text or "") .. line
        end
    end

    -- Добавляем последний блок
    if block_num and current_block.text then
        blocks[block_num] = current_block.text
    end

    return blocks
end

-- Главная функция
function show_ai_dialog(subs, sel, active)
    -- Получаем активную строку
    local line = subs[active]
    local line_index = active

    -- Находим source файл (если нужно)
    local source_blocks = nil
    local video_filename = aegisub.project_properties().video_file

    -- Диалог настроек
    local config = {
        {class="label", label="Контекст (строк до/после):", x=0, y=0},
        {class="intedit", name="context", value=2, min=0, max=10, x=1, y=0},
        {class="checkbox", name="use_source", label="Использовать английский исходник (из папки скрипта)", value=false, x=0, y=1}
    }

    local button, results = aegisub.dialog.display(config, {"OK", "Отмена"})
    if button == "Отмена" then return end

    local context_size = results.context
    local use_source = results.use_source

    -- Парсим source файл
    if use_source then
        local sub_path = aegisub.decode_path("?script")
        local sub_name = aegisub.file_name():match("(.+)%..+$")
        if not sub_name then
             -- Если файл еще не сохранен, попробуем без имени или предупредим
             sub_name = "unknown"
        end
        local source_file = sub_path .. separator .. sub_name .. ".txt"

        -- Если не нашли .txt, попробуем поискать .srt с суффиксом _en или подобное, но пока оставим как в оригинале
        local test_file = io.open(source_file, "r")
        if not test_file then
            -- Попробуем .srt
            source_file = sub_path .. separator .. sub_name .. ".srt"
            test_file = io.open(source_file, "r")
        end

        if test_file then
            test_file:close()
            source_blocks = parse_srt_file(source_file)
        else
            aegisub.log("Внимание: Исходный файл не найден (" .. source_file .. ")\n")
        end
    end

    -- Собираем маппинг индексов строк Aegisub на номера диалогов
    -- Это нужно, так как source_blocks индексируются по порядку (1, 2, 3...)
    -- а subs[] содержит заголовки, стили и т.д.
    local line_id_map = {}
    local dialogue_count = 0

    -- Проходим по всем строкам один раз чтобы построить карту (или можно локально)
    -- Для оптимизации, можно считать только вокруг активной строки, но безопаснее посчитать всё до конца контекста.
    -- Однако, доступ ко всем строкам может быть медленным на огромных скриптах.
    -- Но API Aegisub subs[i] достаточно быстр.

    -- Простая стратегия: считаем диалоги от начала файла до max(active + context).
    local scan_limit = math.min(#subs, line_index + context_size + 100) -- +100 про запас

    for i = 1, scan_limit do
        if subs[i].class == "dialogue" then
            dialogue_count = dialogue_count + 1
            line_id_map[i] = dialogue_count
        end
    end

    -- Собираем контекст
    local context_before = {}
    local context_after = {}

    for i = math.max(1, line_index - context_size), line_index - 1 do
        local ctx_line = subs[i]
        if ctx_line.class == "dialogue" then
            local d_idx = line_id_map[i]
            table.insert(context_before, {
                ru = ctx_line.text,
                en = source_blocks and source_blocks[d_idx] or ""
            })
        end
    end

    for i = line_index + 1, math.min(#subs, line_index + context_size) do
        local ctx_line = subs[i]
        if ctx_line.class == "dialogue" then
            local d_idx = line_id_map[i]
            table.insert(context_after, {
                ru = ctx_line.text,
                en = source_blocks and source_blocks[d_idx] or ""
            })
        end
    end

    -- Формируем запрос
    local current_d_idx = line_id_map[line_index]
    local request_data = {
        current_line = {
            ru = line.text,
            en = source_blocks and source_blocks[current_d_idx] or "",
            duration = (line.end_time - line.start_time) / 1000.0
        },
        context_before = context_before,
        context_after = context_after,
        feedback = ""
    }

    -- Сохраняем JSON
    local req_file = io.open(request_file, "w")
    req_file:write(json.encode(request_data))
    req_file:close()

    -- Запускаем Python
    -- Используем python3 если доступен, иначе python.
    -- В Windows обычно python, в Linux python3.
    -- Можно попробовать определить, но пока оставим python

    local python_cmd = 'python "' .. python_script .. '" "' .. request_file .. '" "' .. response_file .. '" "' .. config_file .. '"'

    aegisub.progress.task("AI думает...")
    -- Aegisub блокируется во время os.execute.
    -- Можно запустить через & (Linux) или start (Windows) но тогда сложно ждать завершения.
    -- Пока используем синхронный вызов.
    local ret = os.execute(python_cmd)

    -- Читаем ответ
    local resp_file = io.open(response_file, "r")
    if not resp_file then
        aegisub.dialog.display({{class="label", label="Ошибка: нет ответа от AI. Проверьте консоль или пути."}}, {"OK"})
        return
    end

    local response_text = resp_file:read("*all")
    resp_file:close()

    if response_text == "" then
         aegisub.dialog.display({{class="label", label="Ошибка: пустой ответ от AI."}}, {"OK"})
         return
    end

    local status, response = pcall(json.decode, response_text)
    if not status then
         aegisub.dialog.display({{class="label", label="Ошибка парсинга JSON: " .. response}}, {"OK"})
         return
    end

    if response.error then
        aegisub.dialog.display({{class="label", label="Ошибка AI: " .. response.error}}, {"OK"})
        return
    end

    -- Показываем результаты
    show_result_dialog(subs, active, line, response.variants, request_data, source_blocks)
end

-- Диалог выбора варианта
function show_result_dialog(subs, active, line, variants, request_data, source_blocks)
    local config = {
        {class="label", label="Оригинал (RU): " .. line.text, x=0, y=0, width=3},
        {class="label", label="Оригинал (EN): " .. (request_data.current_line.en or ""), x=0, y=1, width=3},
        {class="label", label="", x=0, y=2},
        {class="label", label="Выберите вариант:", x=0, y=3, width=3},
    }

    local buttons = {"Применить", "Retry", "Отмена"}

    -- Динамически создаем radio buttons или просто отображаем варианты,
    -- но так как display ограничен, используем textbox для редактирования выбранного.
    -- Чтобы пользователь мог выбрать, добавим dropdown или просто покажем текст
    -- и позволим скопировать в поле редактирования.

    -- Удобнее всего dropdown
    local dropdown_items = {}
    for i, variant in ipairs(variants) do
        table.insert(dropdown_items, variant)
    end

    table.insert(config, {class="dropdown", name="selected_variant", items=dropdown_items, value=dropdown_items[1], x=0, y=4, width=3})

    table.insert(config, {class="label", label="", x=0, y=5})
    table.insert(config, {class="label", label="Редактировать:", x=0, y=6})
    table.insert(config, {class="textbox", name="edited", value=dropdown_items[1], x=0, y=7, width=3, height=4})

    -- При выборе в dropdown значение в textbox не обновится автоматически в реальном времени в старых версиях диалогов Aegisub,
    -- но мы можем попробовать. (Обычно нет).
    -- Поэтому просто предложим выбрать.

    local button, results = aegisub.dialog.display(config, buttons)

    if button == "Отмена" then
        return
    elseif button == "Retry" then
        -- Запрашиваем фидбек
        local feedback_config = {
            {class="label", label="Что не нравится? (например: 'сделай короче', 'более разговорно')", x=0, y=0},
            {class="textbox", name="feedback", value="", width=3, height=2, x=0, y=1}
        }
        local fb_button, fb_results = aegisub.dialog.display(feedback_config, {"Отправить", "Отмена"})

        if fb_button == "Отправить" then
            request_data.feedback = fb_results.feedback

            -- Повторный запрос
            local req_file = io.open(request_file, "w")
            req_file:write(json.encode(request_data))
            req_file:close()

            local python_cmd = 'python "' .. python_script .. '" "' .. request_file .. '" "' .. response_file .. '" "' .. config_file .. '"'
            aegisub.progress.task("AI думает снова...")
            os.execute(python_cmd)

            local resp_file = io.open(response_file, "r")
            if resp_file then
                local response_text = resp_file:read("*all")
                resp_file:close()
                local status, new_response = pcall(json.decode, response_text)
                if status and not new_response.error then
                     show_result_dialog(subs, active, line, new_response.variants, request_data, source_blocks)
                else
                     aegisub.dialog.display({{class="label", label="Ошибка при повторе."}}, {"OK"})
                end
            end
        end
        return
    else
        -- Применяем вариант
        -- Если пользователь редактировал текстбокс, берем его.
        -- Проблема: если он выбрал в dropdown другое значение, но не обновил текстбокс?
        -- Aegisub dialog API limitation.
        -- Давайте доверимся text box. Но начальное значение textbox было первым вариантом.

        -- Лучше просто список кнопок "Вариант 1", "Вариант 2"? Нет, их может быть много текста.

        -- В текущей реализации: берем results.edited.
        -- Если пользователь хочет другой вариант, он должен скопировать его (неудобно) или мы должны сделать цикл диалога.

        -- Улучшение: Добавим кнопку "Выбрать из списка" которая обновит текстбокс? Нет, нельзя обновить диалог без закрытия.

        -- Давайте просто покажем диалог еще раз если выбран dropdown отличается от текста? Нет.

        -- Сделаем проще: "Вариант 1", "Вариант 2", "Вариант 3" как кнопки?
        -- Текст может быть длинным.

        -- Оставим dropdown и textbox. Но предупредим, что редактируется только textbox.
        -- Чтобы выбрать другой вариант для редактирования, пользователю придется перезапустить макрос? Нет.

        -- Попробуем цикл: если выбрано в dropdown что-то отличное от того что было при открытии, переоткрываем диалог с новым value в textbox.
        -- Это работает в Aegisub!

        if results.selected_variant ~= variants[1] and results.edited == variants[1] then
             -- Пользователь сменил dropdown, но не трогал textbox (он равен дефолту).
             -- Переоткроем диалог с новым дефолтом.
             -- (Надо найти индекс варианта)
             local new_variants = {}
             -- Переупорядочим так чтобы выбранный был первым?
             table.insert(new_variants, results.selected_variant)
             for _, v in ipairs(variants) do
                 if v ~= results.selected_variant then
                     table.insert(new_variants, v)
                 end
             end
             show_result_dialog(subs, active, line, new_variants, request_data, source_blocks)
             return
        end

        local final_text = results.edited
        -- Если пользователь выбрал в dropdown другое и отредактировал textbox, мы берем textbox.
        -- Но если он просто выбрал dropdown?
        -- Сложная эвристика.

        -- Давайте просто проверим: если results.edited == variants[1] (исходное значение) И results.selected_variant != variants[1],
        -- значит он хотел выбрать другой.

        -- НО: он мог отредактировать variants[1] так что оно совпало с variants[1]? Нет.

        if results.edited == variants[1] and results.selected_variant ~= variants[1] then
             final_text = results.selected_variant
        end

        line.text = final_text
        subs[active] = line
        aegisub.set_undo_point("AI Subtitle Edit")
    end
end

-- Регистрация макроса
aegisub.register_macro(script_name, script_description, show_ai_dialog)
