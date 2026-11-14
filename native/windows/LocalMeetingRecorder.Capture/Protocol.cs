// JSON-lines wire protocol: see ../../macos/PROTOCOL.md (the one authoritative reference
// for both platforms) and ./PROTOCOL.md (a pointer to it plus Windows-specific notes).
//
// Deliberately uses plain Dictionary<string, object?> payloads, not attribute-decorated
// POCOs + a serializer naming policy — this mirrors main.swift's own [String: Any]
// dictionary approach field-for-field, which made cross-referencing the two
// implementations mechanical rather than guesswork while writing this without being able
// to run either side against the other.

using System.Text.Json;
using System.Text.Json.Nodes;

namespace LocalMeetingRecorder.Capture;

/// <summary>Thread-safe single-writer stdout: every event is exactly one JSON line.</summary>
internal static class Out
{
    private static readonly object WriteLock = new();

    public static void Event(string name, IReadOnlyDictionary<string, object?>? fields = null)
    {
        var obj = new JsonObject { ["event"] = name };
        if (fields is not null)
        {
            foreach (var (key, value) in fields)
            {
                obj[key] = ToJsonNode(value);
            }
        }
        WriteLine(obj.ToJsonString());
    }

    public static void Error(
        string code,
        string message,
        bool fatal = false,
        string? permission = null,
        IReadOnlyDictionary<string, object?>? extra = null)
    {
        var fields = new Dictionary<string, object?>
        {
            ["code"] = code,
            ["message"] = message,
            ["fatal"] = fatal,
        };
        if (permission is not null) fields["permission"] = permission;
        if (extra is not null)
        {
            foreach (var (key, value) in extra) fields[key] = value;
        }
        Event("error", fields);
    }

    private static void WriteLine(string line)
    {
        lock (WriteLock)
        {
            Console.Out.Write(line);
            Console.Out.Write('\n');
            Console.Out.Flush();
        }
    }

    private static JsonNode? ToJsonNode(object? value) => value switch
    {
        null => null,
        JsonNode node => node,
        string s => JsonValue.Create(s),
        bool b => JsonValue.Create(b),
        int i => JsonValue.Create(i),
        long l => JsonValue.Create(l),
        double d => JsonValue.Create(d),
        float f => JsonValue.Create(f),
        IReadOnlyDictionary<string, object?> dict => DictToObject(dict),
        System.Collections.IEnumerable items => ListToArray(items),
        _ => JsonValue.Create(value.ToString()),
    };

    private static JsonObject DictToObject(IReadOnlyDictionary<string, object?> dict)
    {
        var obj = new JsonObject();
        foreach (var (key, value) in dict) obj[key] = ToJsonNode(value);
        return obj;
    }

    private static JsonArray ListToArray(System.Collections.IEnumerable items)
    {
        var arr = new JsonArray();
        foreach (var item in items) arr.Add(ToJsonNode(item));
        return arr;
    }
}

/// <summary>A parsed ``{"cmd": "...", ...}`` line from stdin.</summary>
internal sealed record Command(string Name, JsonObject Fields)
{
    public static Command? TryParse(string line)
    {
        JsonNode? node;
        try
        {
            node = JsonNode.Parse(line);
        }
        catch (JsonException)
        {
            return null;
        }
        if (node is not JsonObject obj) return null;
        if (obj["cmd"] is not JsonValue cmdValue || !cmdValue.TryGetValue(out string? cmd))
        {
            return null;
        }
        return new Command(cmd, obj);
    }

    public string? GetString(string field) =>
        Fields[field] is JsonValue v && v.TryGetValue(out string? s) ? s : null;

    public bool GetBool(string field, bool fallback) =>
        Fields[field] is JsonValue v && v.TryGetValue(out bool b) ? b : fallback;

    public int GetInt(string field, int fallback) =>
        Fields[field] is JsonValue v && v.TryGetValue(out int i) ? i : fallback;

    public double GetDouble(string field, double fallback) =>
        Fields[field] is JsonValue v && v.TryGetValue(out double d) ? d : fallback;
}
