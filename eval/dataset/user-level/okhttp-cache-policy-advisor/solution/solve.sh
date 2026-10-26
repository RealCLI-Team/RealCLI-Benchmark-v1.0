#!/usr/bin/env bash
set -euo pipefail

cd /app
rm -rf okhttp-real-repo
tar -xzf /app/input/okhttp-real-repo-src.tar.gz -C /app
cd /app/okhttp-real-repo/terminal-bench-feature

cat > src/main/java/com/acme/challenge/FeatureService.java <<'JAVA'
package com.acme.challenge;

import java.math.BigDecimal;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
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
    private static final class Num {
        final String raw;

        Num(String raw) {
            this.raw = raw;
        }
    }

    private static String toJson(Object value) {
        StringBuilder sb = new StringBuilder();
        writeJson(value, sb);
        return sb.toString();
    }

    private static void writeJson(Object value, StringBuilder sb) {
        if (value == null) {
            sb.append("null");
        } else if (value instanceof Num) {
            sb.append(((Num) value).raw);
        } else if (value instanceof String) {
            writeString((String) value, sb);
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

    private static Map<String, String> normalizeHeaders(Object headers) {
        Map<String, String> result = new LinkedHashMap<>();
        for (Map.Entry<String, Object> entry : asObject(headers).entrySet()) {
            Object v = entry.getValue();
            result.put(entry.getKey().toLowerCase(Locale.ROOT),
                    v == null ? "" : String.valueOf(v));
        }
        return result;
    }

    private static Map<String, String> parseCacheControl(String value) {
        Map<String, String> directives = new LinkedHashMap<>();
        if (value == null) {
            return directives;
        }
        for (String part : value.split(",")) {
            String token = part.trim();
            if (token.isEmpty()) {
                continue;
            }
            int eq = token.indexOf('=');
            if (eq < 0) {
                directives.put(token.toLowerCase(Locale.ROOT), null);
            } else {
                String name = token.substring(0, eq).trim().toLowerCase(Locale.ROOT);
                String val = token.substring(eq + 1).trim();
                if (val.length() >= 2 && val.startsWith("\"") && val.endsWith("\"")) {
                    val = val.substring(1, val.length() - 1);
                }
                directives.putIfAbsent(name, val);
            }
        }
        return directives;
    }

    private static Long parseMaxAge(String raw) {
        if (raw == null) {
            return null;
        }
        try {
            if (!raw.matches("-?\\d+")) {
                return null;
            }
            return Long.parseLong(raw);
        } catch (NumberFormatException e) {
            return null;
        }
    }

    private static boolean isValidHttpDate(String value) {
        List<DateTimeFormatter> formats = List.of(
                DateTimeFormatter.RFC_1123_DATE_TIME,
                DateTimeFormatter.ofPattern("EEEE, dd-MMM-yy HH:mm:ss zzz", Locale.US),
                DateTimeFormatter.ofPattern("EEE MMM d HH:mm:ss yyyy", Locale.US));
        for (DateTimeFormatter fmt : formats) {
            try {
                ZonedDateTime.parse(value, fmt);
                return true;
            } catch (Exception ignored) {
                // try next format
            }
        }
        return false;
    }

    public String evaluate(String requestJson) {
        Map<String, Object> root = asObject(new JsonParser(requestJson).parse());
        Map<String, Object> request = asObject(root.get("request"));
        Map<String, Object> response = asObject(root.get("response"));
        String method = String.valueOf(request.getOrDefault("method", ""))
                .toUpperCase(Locale.ROOT);
        Map<String, String> requestHeaders = normalizeHeaders(request.get("headers"));
        Map<String, String> responseHeaders = normalizeHeaders(response.get("headers"));
        Map<String, String> cc = parseCacheControl(responseHeaders.get("cache-control"));

        List<String> reasons = new ArrayList<>();
        boolean cacheable = true;
        long freshness = 0;

        if (!method.equals("GET") && !method.equals("HEAD")) {
            reasons.add("method " + method + " is not cacheable");
            cacheable = false;
        } else {
            boolean hasAuthorization = requestHeaders.containsKey("authorization");
            boolean isPublic = cc.containsKey("public");
            boolean noStore = cc.containsKey("no-store");
            boolean isPrivate = cc.containsKey("private");

            if (hasAuthorization) {
                if (isPublic) {
                    reasons.add("authorization allowed by public");
                } else {
                    reasons.add("authorization not allowed");
                    cacheable = false;
                }
            }
            if (noStore) {
                reasons.add("no-store directive");
                cacheable = false;
            } else {
                if (isPrivate) {
                    reasons.add("private directive");
                    cacheable = false;
                }
                if (cc.containsKey("max-age")) {
                    Long maxAge = parseMaxAge(cc.get("max-age"));
                    if (maxAge == null || maxAge < 0) {
                        reasons.add("invalid max-age directive");
                    } else {
                        freshness = maxAge;
                        reasons.add("explicit max-age");
                    }
                }
            }
            String expires = responseHeaders.get("expires");
            if (expires != null && !isValidHttpDate(expires)) {
                reasons.add("invalid Expires header");
            }
        }

        if (!cacheable) {
            freshness = 0;
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("cacheable", cacheable);
        out.put("freshnessSeconds", new Num(String.valueOf(freshness)));
        out.put("reasons", reasons);
        return toJson(out);
    }
}
JAVA

mvn -q test
