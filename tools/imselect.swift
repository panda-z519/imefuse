// imselect <substring>  — switch the current input source by id substring.
// Uses the public Carbon Text Input Source API; needs no special permission.
import Carbon
import Foundation

func list() -> [TISInputSource] {
    guard let cf = TISCreateInputSourceList(nil, false)?.takeRetainedValue() as? [TISInputSource] else {
        return []
    }
    return cf
}

func sid(_ s: TISInputSource) -> String {
    guard let p = TISGetInputSourceProperty(s, kTISPropertyInputSourceID) else { return "" }
    return Unmanaged<CFString>.fromOpaque(p).takeUnretainedValue() as String
}

let args = CommandLine.arguments
if args.count < 2 || args[1] == "--list" {
    for s in list() { print(sid(s)) }
    exit(0)
}
let needle = args[1].lowercased()
var matched: TISInputSource? = nil
for s in list() where sid(s).lowercased().contains(needle) {
    matched = s
    break
}
guard let target = matched else {
    FileHandle.standardError.write("no input source matching \(needle)\n".data(using: .utf8)!)
    exit(1)
}
let st = TISSelectInputSource(target)
print("selected \(sid(target)) status=\(st)")
