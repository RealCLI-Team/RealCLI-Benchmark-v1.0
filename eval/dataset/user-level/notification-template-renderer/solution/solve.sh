#!/usr/bin/env bash
set -euo pipefail

cd /app
rm -rf notification-template-renderer
tar -xzf /app/input/notification-template-renderer-src.tar.gz -C /app
cd /app/notification-template-renderer

cat > src/main/java/com/acme/challenge/FeatureService.java <<'JAVA'
package com.acme.challenge;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

public class FeatureService {

    // ---------- minimal JSON parser ----------
    private static final class JsonParser {
        private final String text;
        private int pos;

        JsonParser(String text) {
            this.text = text;
        }

        Object parse() {
            Object value = parseValue();
            skipWs();
            if (pos != text.length()) {
                throw new IllegalArgumentException("trailing content at " + pos);
            }
            return value;
        }

        private void skipWs() {
            while (pos < text.length()) {
                char c = text.charAt(pos);
                if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
                    pos++;
                } else {
                    break;
                }
            }
        }

        private Object parseValue() {
            skipWs();
            if (pos >= text.length()) {
                throw new IllegalArgumentException("unexpected end of input");
            }
            char c = text.charAt(pos);
            switch (c) {
                case '{': return parseObject();
                case '[': return parseArray();
                case '"': return parseString();
                case 't': expect("true"); return Boolean.TRUE;
                case 'f': expect("false"); return Boolean.FALSE;
                case 'n': expect("null"); return null;
                default: return parseNumber();
            }
        }

        private void expect(String keyword) {
            if (!text.startsWith(keyword, pos)) {
                throw new IllegalArgumentException("bad literal at " + pos);
            }
            pos += keyword.length();
        }

        private Map<String, Object> parseObject() {
            Map<String, Object> map = new LinkedHashMap<>();
            pos++;
            skipWs();
            if (pos < text.length() && text.charAt(pos) == '}') {
                pos++;
                return map;
            }
            while (true) {
                skipWs();
                String key = parseString();
                skipWs();
                if (text.charAt(pos) != ':') {
                    throw new IllegalArgumentException("expected ':' at " + pos);
                }
                pos++;
                map.put(key, parseValue());
                skipWs();
                char c = text.charAt(pos++);
                if (c == '}') {
                    return map;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("expected ',' at " + (pos - 1));
                }
            }
        }

        private List<Object> parseArray() {
            List<Object> list = new ArrayList<>();
            pos++;
            skipWs();
            if (pos < text.length() && text.charAt(pos) == ']') {
                pos++;
                return list;
            }
            while (true) {
                list.add(parseValue());
                skipWs();
                char c = text.charAt(pos++);
                if (c == ']') {
                    return list;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("expected ',' at " + (pos - 1));
                }
            }
        }

        private String parseString() {
            if (text.charAt(pos) != '"') {
                throw new IllegalArgumentException("expected string at " + pos);
            }
            pos++;
            StringBuilder sb = new StringBuilder();
            while (true) {
                char c = text.charAt(pos++);
                if (c == '"') {
                    return sb.toString();
                }
                if (c == '\\') {
                    char e = text.charAt(pos++);
                    switch (e) {
                        case '"': sb.append('"'); break;
                        case '\\': sb.append('\\'); break;
                        case '/': sb.append('/'); break;
                        case 'b': sb.append('\b'); break;
                        case 'f': sb.append('\f'); break;
                        case 'n': sb.append('\n'); break;
                        case 'r': sb.append('\r'); break;
                        case 't': sb.append('\t'); break;
                        case 'u':
                            sb.append((char) Integer.parseInt(text.substring(pos, pos + 4), 16));
                            pos += 4;
                            break;
                        default: throw new IllegalArgumentException("bad escape \\" + e);
                    }
                } else {
                    sb.append(c);
                }
            }
        }

        private Object parseNumber() {
            int start = pos;
            while (pos < text.length() && "+-0123456789.eE".indexOf(text.charAt(pos)) >= 0) {
                pos++;
            }
            if (start == pos) {
                throw new IllegalArgumentException("bad number at " + pos);
            }
            return new BigDecimal(text.substring(start, pos));
        }
    }

    // ---------- compact JSON writer with lexicographically sorted object keys ----------
    private static String toJson(Object value) {
        StringBuilder sb = new StringBuilder();
        writeJson(value, sb);
        return sb.toString();
    }

    private static void writeJson(Object value, StringBuilder sb) {
        if (value == null) {
            sb.append("null");
        } else if (value instanceof String) {
            writeString((String) value, sb);
        } else if (value instanceof BigDecimal) {
            sb.append(((BigDecimal) value).toPlainString());
        } else if (value instanceof Boolean) {
            sb.append(value);
        } else if (value instanceof Map) {
            Map<String, Object> map = (Map<String, Object>) value;
            sb.append('{');
            boolean first = true;
            for (Map.Entry<String, Object> entry : new TreeMap<>(map).entrySet()) {
                if (!first) {
                    sb.append(',');
                }
                first = false;
                writeString(entry.getKey(), sb);
                sb.append(':');
                writeJson(entry.getValue(), sb);
            }
            sb.append('}');
        } else if (value instanceof List) {
            sb.append('[');
            boolean first = true;
            for (Object item : (List<Object>) value) {
                if (!first) {
                    sb.append(',');
                }
                first = false;
                writeJson(item, sb);
            }
            sb.append(']');
        } else {
            throw new IllegalArgumentException("unsupported value: " + value.getClass());
        }
    }

    private static void writeString(String s, StringBuilder sb) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
            }
        }
        sb.append('"');
    }

    // ---------- feature logic ----------
    @SuppressWarnings("unchecked")
    private static Map<String, Object> asObject(Object value) {
        return value instanceof Map ? (Map<String, Object>) value : new LinkedHashMap<>();
    }

    private static String asString(Object value) {
        if (value == null) {
            return null;
        }
        if (value instanceof String) {
            return (String) value;
        }
        if (value instanceof BigDecimal) {
            return ((BigDecimal) value).toPlainString();
        }
        return String.valueOf(value);
    }

    private static String htmlEscape(String value) {
        StringBuilder sb = new StringBuilder(value.length());
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '&': sb.append("&amp;"); break;
                case '<': sb.append("&lt;"); break;
                case '>': sb.append("&gt;"); break;
                case '"': sb.append("&quot;"); break;
                case '\'': sb.append("&#39;"); break;
                default: sb.append(c);
            }
        }
        return sb.toString();
    }

    public String evaluate(String requestJson) {
        Map<String, Object> request = asObject(new JsonParser(requestJson).parse());
        String channel = asString(request.get("channel"));
        String locale = asString(request.get("locale"));
        Map<String, Object> data = asObject(request.get("data"));
        Map<String, Object> templates = asObject(request.get("templates"));

        boolean channelOk = "email".equals(channel) || "sms".equals(channel);
        List<String> errors = new ArrayList<>();
        if (!channelOk) {
            errors.add("unsupported channel " + channel);
        }
        if (locale == null || locale.isEmpty()) {
            errors.add("locale must not be blank");
        }
        if (templates.isEmpty()) {
            errors.add("templates must not be empty");
        }
        if (!errors.isEmpty()) {
            Map<String, Object> response = new LinkedHashMap<>();
            response.put("errors", errors);
            response.put("missing", new ArrayList<>());
            response.put("rendered", "");
            return toJson(response);
        }

        String selectedKey = null;
        if (templates.containsKey(locale)) {
            selectedKey = locale;
        } else {
            int dash = locale.indexOf('-');
            if (dash > 0 && templates.containsKey(locale.substring(0, dash))) {
                selectedKey = locale.substring(0, dash);
            } else if (templates.containsKey("en")) {
                selectedKey = "en";
            }
        }
        String template = selectedKey == null ? "" : asString(templates.get(selectedKey));

        boolean escape = "email".equals(channel);
        StringBuilder rendered = new StringBuilder();
        List<String> missing = new ArrayList<>();
        int i = 0;
        while (i < template.length()) {
            int start = template.indexOf("{{", i);
            if (start < 0) {
                rendered.append(template, i, template.length());
                break;
            }
            rendered.append(template, i, start);
            int end = template.indexOf("}}", start + 2);
            if (end < 0) {
                rendered.append(template.substring(start));
                break;
            }
            String token = template.substring(start + 2, end);
            String name = token;
            String fallback = null;
            int bar = token.indexOf('|');
            if (bar >= 0) {
                name = token.substring(0, bar);
                fallback = token.substring(bar + 1);
            }
            if (data.containsKey(name)) {
                String value = asString(data.get(name));
                rendered.append(escape ? htmlEscape(value) : value);
            } else if (fallback != null) {
                rendered.append(escape ? htmlEscape(fallback) : fallback);
            } else if (!missing.contains(name)) {
                missing.add(name);
            }
            i = end + 2;
        }
        Collections.sort(missing);

        Map<String, Object> response = new LinkedHashMap<>();
        response.put("fallbackLocale", selectedKey);
        response.put("missing", missing);
        response.put("rendered", rendered.toString());
        return toJson(response);
    }
}
JAVA

mvn -q test
