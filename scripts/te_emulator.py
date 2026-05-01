import sys
import os
import argparse
import time
from te_linter import Lexer, Parser, Program, Block, Assign, BinOp, UnOp, Literal, Identifier, MemberAccess, ArrayAccess, Call

# ============================================================================
# Emulator Types
# ============================================================================

class TEBreak(Exception): pass
class TEExit(Exception): pass
class TEError(Exception): pass

class TEObject:
    def __init__(self, value):
        self.value = value

    def call_member(self, name, args, evaluator):
        method_name = f"te_{name}"
        if hasattr(self, method_name):
            func = getattr(self, method_name)
            # Foreach is special, needs evaluator context
            if name == "foreach":
                return func(args, evaluator)
            return func(*args)
        raise TEError(f"Unknown method '{name}' on {self.__class__.__name__}")

    def is_truthy(self):
        if isinstance(self.value, int): return self.value != 0
        if isinstance(self.value, list): return len(self.value) > 0
        if isinstance(self.value, str): return len(self.value) > 0
        return bool(self.value)

    __bool__ = is_truthy

    def __str__(self):
        return str(self.value)

class TEInt(TEObject):
    def te_str(self):
        return TEString(str(self.value))

class TEString(TEObject):
    def te_str(self):
        return self
    def te_len(self):
        return TEInt(len(self.value))
    def te_bytes(self):
        return TEByteArray(bytearray(self.value, 'utf-8'))

class TEByteArray(TEObject):
    def te_len(self):
        return TEInt(len(self.value))
    def te_project(self):
        return self
    def te_slice(self, start, length):
        s = start.value
        l = length.value
        return TEByteArray(bytearray(self.value[s:s+l]))
    def te_find(self, target_arr):
        # Target is passed as an array literal ["BYTE[]", 0x70, ...]
        t_bytes = bytearray([x.value if isinstance(x, TEObject) else x for x in target_arr.value[1:] if isinstance(x.value, int)])
        idx = self.value.find(t_bytes)
        return TEInt(idx)
    def te_str(self):
        return TEString(f"ByteArray(len={len(self.value)})")
    def te_bytestostr(self):
        s = self.value.decode('utf-8', errors='ignore').rstrip('\x00')
        return TEString(s)

class TEArray(TEObject):
    def te_len(self):
        return TEInt(len(self.value))
    def te_copy(self):
        return TEArray(list(self.value))
    def te_add(self, item):
        self.value.append(item)
        return self
    def te_foreach(self, args, evaluator):
        var_name = args[0].value
        block = args[1].value # Expecting TEFunction/Block
        for val in self.value:
            evaluator.env[var_name] = val
            evaluator.execute_block(block)
        return TEInt(0)

class TEDict(TEObject):
    def __init__(self):
        super().__init__({})
    def call_member(self, name, args, evaluator):
        # In TE, dict member access functions as key retrieval/setting
        if len(args) == 0:
            return self.value.get(name, TEInt(0))
        elif len(args) == 1:
            self.value[name] = args[0]
            return TEInt(0)
        return super().call_member(name, args, evaluator)

class TESave(TEObject):
    def __init__(self, path, root_fs):
        super().__init__(path)
        self.path = path
        self.root_fs = root_fs
        
    def te_read(self, filepath):
        fp = filepath.value.lstrip('/')
        
        # Scenario 1: User manually extracted the save as a folder locally
        if os.path.isdir(self.root_fs):
            disk_path = os.path.join(self.root_fs, fp)
            if os.path.exists(disk_path):
                with open(disk_path, 'rb') as f:
                    return TEByteArray(bytearray(f.read()))
                    
        # Scenario 2: Standard architecture (raw DISF file mounted from SYSTEM)
        elif os.path.isfile(self.root_fs):
            with open(self.root_fs, 'rb') as f:
                data = bytearray(f.read())
            
            # Since we don't have a 1:1 DISF parser in Python to extract the exact file bytes,
            # we check if the requested file name exists inside the raw container.
            # If so, we return the entire container. The TE script's JSON scanner and raw
            # binary scanner fallbacks are robust enough to find the PIN within the full array.
            filename_bytes = fp.encode('utf-8') + b'\x00'
            if data.find(filename_bytes) >= 0 or data.find(fp.encode('utf-8')) >= 0:
                return TEByteArray(data)
                
            return TEByteArray(bytearray())
            
        return TEByteArray(bytearray())
        
    def te_commit(self):
        return TEInt(0)

class TEFunction(TEObject):
    def __init__(self, node):
        super().__init__(node) # node is a Block AST

class TEElseWrapper(TEObject):
    def __init__(self, executed):
        super().__init__(executed)
        
    def call_member(self, name, args, evaluator):
        if name == "else":
            # The AST evaluates to a chained call. We return an executor func 
            # that receives the block and processes it if the "if" statement failed.
            def else_executor(te_func):
                if not self.value: # if 'if' was not executed
                    evaluator.execute_block(te_func.value)
                return TEInt(0)
            return else_executor
        raise TEError(f"Unknown method {name}")

# ============================================================================
# Evaluator (Interpreter)
# ============================================================================

class Evaluator:
    def __init__(self, mock_fs_root="mock_fs"):
        self.env = {}
        self.mock_fs_root = mock_fs_root
        
        # Setup Standard Library Globals
        self.env["print"] = self.std_print
        self.env["println"] = self.std_println
        self.env["exit"] = self.std_exit
        self.env["pause"] = self.std_pause
        self.env["clear"] = self.std_clear
        self.env["color"] = self.std_color
        self.env["menu"] = self.std_menu
        self.env["fsexists"] = self.std_fsexists
        self.env["readfile"] = self.std_readfile
        self.env["readsave"] = self.std_readsave
        self.env["mountsys"] = self.std_mountsys
        self.env["mountemu"] = self.std_mountemu
        self.env["dict"] = self.std_dict

    def resolve(self, name):
        if name in self.env:
            return self.env[name]
        return TEInt(0) # Undeclared vars are initialized to 0 in TegraScript

    def execute_block(self, block_node):
        res = TEInt(0)
        for stmt in block_node.statements:
            res = self.visit(stmt)
        return res

    def visit(self, node):
        if not node:
            return TEInt(0)

        if isinstance(node, Program):
            return self.execute_block(node)
        
        elif isinstance(node, Block):
            # Evaluate naked blocks as anonymous TEFunctions
            return TEFunction(node)

        elif isinstance(node, Assign):
            val = self.visit(node.value)
            if isinstance(node.target, Identifier):
                self.env[node.target.name] = val
            elif isinstance(node.target, ArrayAccess):
                target_arr = self.visit(node.target.target)
                idx = self.visit(node.target.index)
                if isinstance(target_arr, TEArray):
                    target_arr.value[idx.value] = val
                elif isinstance(target_arr, TEByteArray):
                    target_arr.value[idx.value] = val.value
            return val

        elif isinstance(node, Identifier):
            val = self.resolve(node.name)
            return val 

        elif isinstance(node, Literal):
            if node.type_name == "Int": return TEInt(int(node.val, 0) if isinstance(node.val, str) else node.val)
            if node.type_name == "String": return TEString(node.val)
            if node.type_name == "Array":
                return TEArray([self.visit(e) for e in node.val])
            return TEInt(0)

        elif isinstance(node, BinOp):
            l = self.visit(node.left)
            r = self.visit(node.right)
            
            # String handling
            if isinstance(l, TEString) or isinstance(r, TEString):
                if node.op == '+': return TEString(str(l.value) + str(r.value))
                if node.op == '==': return TEInt(1 if str(l.value) == str(r.value) else 0)
                if node.op == '!=': return TEInt(1 if str(l.value) != str(r.value) else 0)
                
            lval = l.value if isinstance(l, TEObject) else l
            rval = r.value if isinstance(r, TEObject) else r
            
            # Math & Logic
            if node.op == '+': return TEInt(lval + rval)
            if node.op == '-': return TEInt(lval - rval)
            if node.op == '*': return TEInt(lval * rval)
            if node.op == '/': return TEInt(lval // rval)
            if node.op == '%': return TEInt(lval % rval)
            if node.op == '==': return TEInt(1 if lval == rval else 0)
            if node.op == '!=': return TEInt(1 if lval != rval else 0)
            if node.op == '<': return TEInt(1 if lval < rval else 0)
            if node.op == '<=': return TEInt(1 if lval <= rval else 0)
            if node.op == '>': return TEInt(1 if lval > rval else 0)
            if node.op == '>=': return TEInt(1 if lval >= rval else 0)
            if node.op == '&&': return TEInt(1 if lval and rval else 0)
            if node.op == '||': return TEInt(1 if lval or rval else 0)
            raise TEError(f"Unsupported BinOp {node.op}")

        elif isinstance(node, UnOp):
            r = self.visit(node.right)
            rval = r.value if isinstance(r, TEObject) else r
            if node.op == '!': return TEInt(1 if not rval else 0)
            if node.op == '-': return TEInt(-rval)
            return TEInt(0)

        elif isinstance(node, MemberAccess):
            target = self.visit(node.target)
            # Create a bound method wrapper bridging Python to TEObject logic
            def bound_method(*args):
                return target.call_member(node.member, args, self)
            return bound_method

        elif isinstance(node, ArrayAccess):
            target = self.visit(node.target)
            idx = self.visit(node.index).value
            # Real TE prints "[FATAL] Accessing index N while array is M long"
            # via SCRIPT_FATAL_ERR followed by "Error occured on or near line L".
            # Mirror that format so script bugs look identical here and on device.
            if isinstance(target, TEArray):
                if idx < 0 or idx >= len(target.value):
                    line = getattr(node.index, 'line', None) or getattr(node, 'line', '?')
                    raise TEError(f"[FATAL] Accessing index {idx} while array is {len(target.value)} long\nError occured on or near line {line}")
                return target.value[idx]
            if isinstance(target, TEByteArray):
                if idx < 0 or idx >= len(target.value):
                    line = getattr(node.index, 'line', None) or getattr(node, 'line', '?')
                    raise TEError(f"[FATAL] Accessing index {idx} while array is {len(target.value)} long\nError occured on or near line {line}")
                return TEInt(target.value[idx])
            raise TEError("Cannot index object")

        elif isinstance(node, Call):
            # Special Handling for while/if syntax structures in the AST
            if isinstance(node.target, Call) and isinstance(node.target.target, Identifier):
                if node.target.target.name == "while":
                    cond_node = node.target.args[0]
                    block_node = node.args[0]
                    cond = self.visit(cond_node)
                    while cond.is_truthy():
                        self.execute_block(block_node)
                        cond = self.visit(cond_node)
                    return TEInt(0)
                
                elif node.target.target.name == "if":
                    cond_node = node.target.args[0]
                    block_node = node.args[0]
                    cond = self.visit(cond_node)
                    executed = False
                    if cond.is_truthy():
                        self.execute_block(block_node)
                        executed = True
                    return TEElseWrapper(executed)

            # Standard Function Calls
            func = self.visit(node.target)
            
            evaluated_args = []
            for arg_node in getattr(node, 'args', []):
                if arg_node:
                    evaluated_args.append(self.visit(arg_node))

            if callable(func):
                return func(*evaluated_args)
            elif isinstance(func, TEFunction):
                self.execute_block(func.value)
                return TEInt(0)
            else:
                raise TEError("Target is not callable")

        return TEInt(0)

    # ==================================================
    # Standard Library Mock Implementations
    # ==================================================

    def std_print(self, *args):
        for arg in args:
            print(str(arg.value), end="")
        return TEInt(0)

    def std_println(self, *args):
        for arg in args:
            print(str(arg.value), end="")
        print()
        return TEInt(0)

    def std_exit(self):
        print("\n[EMU] Script exited via exit().")
        raise TEExit()

    def std_pause(self):
        input("[EMU] Paused. Press Enter to continue...")
        return TEInt(0)

    def std_clear(self):
        # os.system('cls' if os.name == 'nt' else 'clear')
        print("[EMU] Clearing screen...")
        return TEInt(0)

    def std_color(self, hex_val):
        code = hex_val.value
        if code == 0xFF0000:
            print("\033[91m", end="") # Red
        elif code == 0x00FF00:
            print("\033[92m", end="") # Green
        elif code == 0xFFFFFF:
            print("\033[0m", end="")  # Reset
        return TEInt(0)

    def std_menu(self, options, default=TEInt(0)):
        print("\n=== SYSTEM MENU ===")
        for i, opt in enumerate(options.value):
            print(f"[{i}] {opt.value}")
        print("===================")
        try:
            choice = int(input("> "))
        except ValueError:
            choice = default.value
        return TEInt(choice)

    def std_fsexists(self, path):
        real_path = os.path.join(self.mock_fs_root, path.value.replace("bis:/", ""))
        return TEInt(1 if os.path.exists(real_path) else 0)

    def std_readsave(self, path):
        real_path = os.path.join(self.mock_fs_root, path.value.replace("bis:/", ""))
        return TESave(path.value, real_path)

    def std_readfile(self, path):
        real_path = os.path.join(self.mock_fs_root, path.value.replace("bis:/", ""))
        if os.path.isdir(real_path):
            # Synthesize a save container buffer from mock directory contents
            raw_buf = bytearray()
            for filename in os.listdir(real_path):
                raw_buf.extend(filename.encode('utf-8'))
                raw_buf.append(0)
            return TEByteArray(raw_buf)
            
        if os.path.exists(real_path):
            with open(real_path, 'rb') as f:
                return TEByteArray(bytearray(f.read()))
        return TEByteArray(bytearray())

    def std_mountsys(self, part):
        print(f"[EMU/Mount] Mounted SysMMC: {part.value}")
        return TEInt(0)

    def std_mountemu(self, part):
        print(f"[EMU/Mount] Mounted EmuMMC: {part.value}")
        return TEInt(0)

    def std_dict(self):
        return TEDict()


def setup_mock_data(mock_fs_root):
    """Generates dummy files locally so the emulator successfully finds data."""
    save_dir = os.path.join(mock_fs_root, "save")
    
    # Ensure the parent 'save' directory exists, but do not touch the file path itself yet
    if not os.path.exists(save_dir):
        os.makedirs(save_dir, exist_ok=True)
        
    save_file = os.path.join(save_dir, "8000000000000100")
    
    # If the user placed a real raw save file here, don't overwrite it or crash
    if not os.path.exists(save_file):
        print(f"[EMU] No save file found at {save_file}!")
        exit(0)

def run_emulator(filepath, mock_fs_root="mock_fs"):
    with open(filepath, 'r') as f:
        src = f.read()
    
    # Compile
    lexer = Lexer(src)
    tokens = lexer.tokenize()
    parser = Parser(tokens)
    ast = parser.parse_program()
    
    # Environment Setup
    setup_mock_data(mock_fs_root)
    evaluator = Evaluator(mock_fs_root)
    
    # Execute
    print(f"--- TegraScript Execution Started: {os.path.basename(filepath)} ---")
    try:
        evaluator.visit(ast)
    except TEExit:
        pass # Clean exit via exit()
    except Exception as e:
        import traceback
        print(f"\n--- Runtime Error: {e} ---")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TegraScript Python Emulator")
    parser.add_argument("script", help="Path to the .te script to run")
    parser.add_argument("--mock-fs", default="mock_fs", help="Directory acting as the root filesystem (e.g. mounted SYSTEM partition)")
    args = parser.parse_args()
    
    if not os.path.exists(args.script):
        print(f"File not found: {args.script}")
        sys.exit(1)
        
    os.makedirs(args.mock_fs, exist_ok=True)
    run_emulator(args.script, args.mock_fs)