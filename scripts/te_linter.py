import sys
import os
import argparse
from dataclasses import dataclass
from typing import List, Optional, Any, Set, Dict

try:
    from rich.console import Console
    from rich.tree import Tree
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


# ============================================================================
# Constants and Valid Identifiers
# ============================================================================

VALID_METHODS_BY_TYPE = {
    "Int": {"str", "print", "not"},
    "String": {"str", "print", "len", "bytes", "get", "split"},
    "Array": {"str", "add", "len", "project", "get", "slice", "foreach", "copy", "set", "addrange", "contains", "bytestostr", "find"},
    "Dict": {"str", "set"},
    "Save": {"str", "read", "write", "commit", "resize"},
    "Function": {"str", "else"}
}

VALID_METHODS: Set[str] = set()
for methods in VALID_METHODS_BY_TYPE.values():
    VALID_METHODS.update(methods)

VALID_GLOBALS: Set[str] = {
    "if", "while", "exit", "break", "readsave", "dict", "print", "println", "printpos", 
    "setpixel", "setpixels", "emu", "cwd", "clear", "timer", "pause", "hidread", "color", 
    "menu", "power", "sleep", "mountsys", "mountemu", "ncatype", "emmcread", "emmcwrite", 
    "emummcread", "emummcwrite", "fuse_patched", "fuse_hwtype", "readdir", "deldir", 
    "mkdir", "copydir", "copyfile", "movefile", "delfile", "readfile", "getfilesize", 
    "writefile", "payload", "combinepath", "escapepath", "fsexists"
}

# ============================================================================
# Lexical Analysis (Tokenizer)
# ============================================================================

@dataclass
class Token:
    """Represents a single lexical token from the source code."""
    type: str
    value: str
    line: int

class Lexer:
    """Converts raw TegraScript source code into a stream of Tokens."""
    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.line = 1
        self.tokens: List[Token] = []

    def tokenize(self) -> List[Token]:
        while self.pos < len(self.text):
            char = self.text[self.pos]

            if char in ' \t\r':
                self.pos += 1
                continue
                
            if char == '\n':
                self.line += 1
                self.pos += 1
                continue
                
            if char == '#':
                self._skip_comment()
                continue
                
            if char == '"':
                self._consume_string()
            elif char.isdigit():
                self._consume_number()
            elif char.isalpha() or char == '_':
                self._consume_identifier()
            else:
                self._consume_operator_or_symbol(char)
                
        self.tokens.append(Token('EOF', '', self.line))
        return self.tokens

    def _skip_comment(self):
        """Skips characters until the end of the line."""
        while self.pos < len(self.text) and self.text[self.pos] != '\n':
            self.pos += 1

    def _consume_string(self):
        """Parses a string literal, handling escaped quotes."""
        start_line = self.line
        self.pos += 1  # Skip opening quote
        value = ""
        
        while self.pos < len(self.text) and self.text[self.pos] != '"':
            if self.text[self.pos] == '\\':
                self.pos += 1
                if self.pos < len(self.text):
                    value += '\\' + self.text[self.pos]
                    self.pos += 1
            else:
                if self.text[self.pos] == '\n':
                    self.line += 1
                value += self.text[self.pos]
                self.pos += 1
                
        if self.pos < len(self.text) and self.text[self.pos] == '"':
            self.pos += 1  # Skip closing quote
            
        self.tokens.append(Token('STRING', value, start_line))

    def _consume_number(self):
        """Parses an integer or hex literal."""
        start_line = self.line
        value = self.text[self.pos]
        self.pos += 1
        
        while self.pos < len(self.text) and (self.text[self.pos].isdigit() or self.text[self.pos] in 'xXabcdefABCDEF'):
            value += self.text[self.pos]
            self.pos += 1
            
        self.tokens.append(Token('NUMBER', value, start_line))

    def _consume_identifier(self):
        """Parses a variable or function name."""
        start_line = self.line
        value = self.text[self.pos]
        self.pos += 1
        
        while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] == '_'):
            value += self.text[self.pos]
            self.pos += 1
            
        self.tokens.append(Token('ID', value, start_line))

    def _consume_operator_or_symbol(self, char: str):
        """Parses mathematical operators, logical operators, and brackets."""
        start_line = self.line
        self.pos += 1
        next_char = self.text[self.pos] if self.pos < len(self.text) else None
        
        # Two-character operators
        if char in '=!<>+-*/&|':
            if next_char and (char + next_char) in ('==', '!=', '<=', '>=', '&&', '||'):
                self.pos += 1
                self.tokens.append(Token('OP', char + next_char, start_line))
                return
            elif char in '=!<>+-*/':
                self.tokens.append(Token(char, char, start_line))
                return
                
        # Single-character symbols
        if char in '(){}[]=.,!':
            self.tokens.append(Token(char, char, start_line))
        else:
            # Skip unknown character
            pass

# ============================================================================
# Abstract Syntax Tree (AST) Nodes
# ============================================================================

class Node:
    pass

@dataclass
class Program(Node):
    statements: List[Node]

@dataclass
class Assign(Node):
    target: Node
    value: Node

@dataclass
class Identifier(Node):
    name: str
    line: int

@dataclass
class MemberAccess(Node):
    target: Node
    member: str
    line: int

@dataclass
class ArrayAccess(Node):
    target: Node
    index: Node
    line: int

@dataclass
class Call(Node):
    target: Node
    args: List[Node]

@dataclass
class Block(Node):
    statements: List[Node]

@dataclass
class BinOp(Node):
    left: Node
    op: str
    right: Node

@dataclass
class UnOp(Node):
    op: str
    right: Node

@dataclass
class Literal(Node):
    val: Any
    type_name: str = "Unknown"

# ============================================================================
# Parser (Recursive Descent)
# ============================================================================

class Parser:
    """Constructs an AST from a token stream."""
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    def current(self) -> Token:
        return self.tokens[self.pos]

    def consume(self, expected_type: Optional[str] = None) -> Token:
        tok = self.current()
        if expected_type and tok.type != expected_type:
            print(f"[ERROR] Syntax Error on line {tok.line}: Expected '{expected_type}', got '{tok.type}' ('{tok.value}')")
            self.pos += 1
        else:
            self.pos += 1
        return tok

    def parse_program(self) -> Program:
        statements = []
        while self.current().type != 'EOF':
            stmt = self.parse_statement()
            if stmt:
                statements.append(stmt)
        return Program(statements)

    def parse_statement(self) -> Optional[Node]:
        return self.parse_expr()

    def parse_expr(self) -> Node:
        # Real TE (ABadIdeaV3) has NO operator precedence: its eval() walks
        # operators strictly left-to-right inside an "EquationSeperator"
        # (statement) boundary, applying each via callMemberFunctionDirect on
        # the running accumulator. To make this emulator surface the same
        # bugs (e.g. `i < max && found == 0` parses as `((i < max) && found)
        # == 0` on real TE), we mirror that: every binary op has equal
        # precedence and is left-associative. Use parens to group.
        expr = self.parse_binop_flat()
        if self.current().type == '=':
            self.consume('=')
            val = self.parse_expr()
            return Assign(expr, val)
        return expr

    def parse_binop_flat(self) -> Node:
        left = self.parse_primary()
        while self._is_binary_op(self.current()):
            op = self.consume().value
            right = self.parse_primary()
            left = BinOp(left, op, right)
        return left

    @staticmethod
    def _is_binary_op(tok) -> bool:
        # Two-char ops are emitted as type='OP'. Single-char arithmetic and
        # relational tokens are emitted with their own type (e.g. '+'/'<').
        if tok.type == 'OP' and tok.value in ('||', '&&', '==', '!=', '<=', '>='):
            return True
        if tok.type in ('+', '-', '*', '/', '<', '>'):
            return True
        return False

    def parse_primary(self) -> Optional[Node]:
        tok = self.current()
        
        if tok.type == 'ID':
            self.consume()
            node = Identifier(tok.value, tok.line)
            return self.parse_postfix(node)
            
        elif tok.type == 'NUMBER':
            self.consume()
            return self.parse_postfix(Literal(tok.value, "Int"))
            
        elif tok.type == 'STRING':
            self.consume()
            return self.parse_postfix(Literal(tok.value, "String"))
            
        elif tok.type == '(':
            self.consume()
            node = self.parse_expr()
            if self.current().type == ')':
                self.consume()
            return self.parse_postfix(node)
            
        elif tok.type == '{':
            self.consume()
            stmts = []
            while self.current().type not in ('}', 'EOF'):
                stmt = self.parse_statement()
                if stmt:
                    stmts.append(stmt)
            if self.current().type == '}':
                self.consume()
            return self.parse_postfix(Block(stmts))
            
        elif tok.type == '[':
            self.consume()
            elems = []
            while self.current().type not in (']', 'EOF'):
                elems.append(self.parse_expr())
                if self.current().type == ',':
                    self.consume()
            if self.current().type == ']':
                self.consume()
            return self.parse_postfix(Literal(elems, "Array"))
            
        elif tok.type in ('!', '-'):
            op = self.consume().type
            right_expr = self.parse_primary()
            return UnOp(op, right_expr)
            
        else:
            self.pos += 1
            return None

    def parse_postfix(self, node: Node) -> Node:
        """Parses method chains, function calls, and array access modifiers attached to a primary node."""
        while True:
            if self.current().type == '.':
                self.consume()
                tok = self.current()
                if tok.type == 'ID':
                    self.consume()
                    node = MemberAccess(node, tok.value, tok.line)
                else:
                    break
                    
            elif self.current().type == '(':
                self.consume()
                args = []
                while self.current().type not in (')', 'EOF'):
                    args.append(self.parse_expr())
                    if self.current().type == ',':
                        self.consume()
                if self.current().type == ')':
                    self.consume()
                node = Call(node, args)
                
            elif self.current().type == '[':
                # Disambiguate ArrayAccess vs New Array ASI:
                # If there is a comma before the closing bracket, it's a new Array Literal statement, NOT an access!
                pos = self.pos
                depth = 0
                has_comma = False
                while pos < len(self.tokens):
                    if self.tokens[pos].type == '[': depth += 1
                    elif self.tokens[pos].type == ']': 
                        depth -= 1
                        if depth == 0: break
                    elif self.tokens[pos].type == ',' and depth == 1:
                        has_comma = True
                        break
                    pos += 1

                if has_comma:
                    # It's a new Array Literal statement separated by ASI. Don't consume it here.
                    break

                line = self.current().line
                self.consume()
                idx = self.parse_expr()
                if self.current().type == ']':
                    self.consume()
                node = ArrayAccess(node, idx, line)
                
            elif self.current().type == '{': 
                # Chaining a block like .else() { ... }
                self.consume()
                stmts = []
                while self.current().type not in ('}', 'EOF'):
                    stmt = self.parse_statement()
                    if stmt:
                        stmts.append(stmt)
                if self.current().type == '}':
                    self.consume()
                node = Call(node, [Block(stmts)])
            else:
                break
                
        return node

# ============================================================================
# Linter (AST Visitor)
# ============================================================================

class Linter:
    """Traverses the AST to perform semantic validation and track scopes."""
    def __init__(self):
        self.errors = 0
        self.assigned_vars: Set[str] = {"args", "REQUIRE", "VER"}
        self.assignment_lines: Dict[str, int] = {} # Tracks where a var was first declared
        self.used_vars: Set[str] = set() # Tracks which vars were actually read
        self.inferred_types: Dict[str, str] = {}

    def _infer_type(self, node: Node) -> str:
        """Infer the base type of a node based on static hints."""
        if isinstance(node, Literal):
            return node.type_name
        elif isinstance(node, Call):
            if isinstance(node.target, Identifier):
                if node.target.name in ("dict", "readdir", "pause"):
                    return "Dict"
                if node.target.name == "readsave":
                    return "Save"
            if isinstance(node.target, MemberAccess):
                if node.target.member == "str": return "String"
                if node.target.member == "int": return "Int"
                if node.target.member == "len": return "Int"
                if node.target.member == "split": return "Array"
                if node.target.member == "hex": return "String"
        elif isinstance(node, Identifier):
            return self.inferred_types.get(node.name, "Unknown")
        return "Unknown"

    def collect_assignments(self, node: Node):
        """First pass: Discover all initialized variables to avoid false positive 'undeclared' warnings."""
        if not node:
            return
            
        if isinstance(node, (Program, Block)):
            for stmt in getattr(node, 'statements', []):
                self.collect_assignments(stmt)
                
        elif isinstance(node, Assign):
            if isinstance(node.target, Identifier):
                self.assigned_vars.add(node.target.name)
                if node.target.name not in self.assignment_lines:
                    self.assignment_lines[node.target.name] = node.target.line
                    
            self.collect_assignments(node.target)
            self.collect_assignments(node.value)
            
        elif isinstance(node, BinOp):
            self.collect_assignments(node.left)
            self.collect_assignments(node.right)
            
        elif isinstance(node, UnOp):
            self.collect_assignments(node.right)
            
        elif isinstance(node, MemberAccess):
            self.collect_assignments(node.target)
            
        elif isinstance(node, ArrayAccess):
            self.collect_assignments(node.target)
            self.collect_assignments(node.index)
            
        elif isinstance(node, Literal):
            if isinstance(node.val, list):
                for element in node.val:
                    self.collect_assignments(element)
                    
        elif isinstance(node, Call):
            # Special case for array.foreach("var") bindings
            if isinstance(node.target, MemberAccess) and node.target.member == "foreach":
                if len(node.args) >= 1 and isinstance(node.args[0], Literal) and node.args[0].type_name == "String":
                    var_name = node.args[0].val
                    self.assigned_vars.add(var_name)
                    if var_name not in self.assignment_lines:
                        self.assignment_lines[var_name] = node.target.line
                    
            self.collect_assignments(node.target)
            for arg in getattr(node, 'args', []): 
                if arg:
                    self.collect_assignments(arg)
            
    def check(self, node: Node):
        """Second pass: Validate symbol definitions and method calls against known definitions."""
        if not node:
            return
            
        if isinstance(node, (Program, Block)):
            for stmt in getattr(node, 'statements', []):
                self.check(stmt)
                
        elif isinstance(node, Assign):
            # Only validate the target if it isn't a direct identifier assignment
            if not isinstance(node.target, Identifier):
                self.check(node.target)
            else:
                self.inferred_types[node.target.name] = self._infer_type(node.value)
            self.check(node.value)
            
        elif isinstance(node, Identifier):
            if node.name not in VALID_GLOBALS and node.name not in self.assigned_vars:
                print(f"[WARN] Line {node.line}: Undeclared variable or function '{node.name}'")
                self.errors += 1
            elif node.name not in VALID_GLOBALS:
                # If it is valid and isn't a builtin, mark it as used!
                self.used_vars.add(node.name)
                
        elif isinstance(node, MemberAccess):
            inferred = self._infer_type(node.target)
            
            if inferred == "Dict":
                pass # Dictionaries in TegraScript fallback member resolution to key lookups
            elif inferred != "Unknown":
                allowed_methods = VALID_METHODS_BY_TYPE.get(inferred, set())
                if node.member not in allowed_methods:
                    print(f"[FATAL] Line {node.line}: Method '.{node.member}()' is not valid for type {inferred}")
                    self.errors += 1
            else:
                if node.member not in VALID_METHODS:
                    print(f"[FATAL] Line {node.line}: Unknown method '.{node.member}()'")
                    self.errors += 1
                    
            self.check(node.target)
            
        elif isinstance(node, Call):
            self.check(node.target)
            for arg in getattr(node, 'args', []):
                if arg:
                    self.check(arg)
                    
        elif isinstance(node, ArrayAccess):
            self.check(node.target)
            self.check(node.index)
            
        elif isinstance(node, BinOp):
            self.check(node.left)
            self.check(node.right)
            
        elif isinstance(node, UnOp):
            self.check(node.right)
            
        elif isinstance(node, Literal):
            if isinstance(node.val, list):
                for element in node.val:
                    self.check(element)

    def report_unused(self):
        """Third pass: Identify and warn about declared variables that were never used."""
        # Built-ins are ignored so they don't trigger false positives
        ignored_vars = {"args", "REQUIRE", "VER"}
        unused = self.assigned_vars - self.used_vars - ignored_vars
        
        for var in sorted(unused):
            line = self.assignment_lines.get(var, "?")
            print(f"[INFO] Line {line}: Variable '{var}' is declared but never used")

# ============================================================================
# Debug Tree Formatting
# ============================================================================

def build_rich_tree(node: Node, tree=None):
    """Recursively constructs a rich Tree object for visualizing the AST."""
    if not RICH_AVAILABLE:
        return None
    
    if node is None:
        if tree:
            tree.add("[red]SyntaxError/None[/red]")
        return tree
        
    if tree is None:
        tree = Tree(f"[bold blue]{node.__class__.__name__}[/bold blue]")
        
    if isinstance(node, (Program, Block)):
        for stmt in getattr(node, 'statements', []):
            child_tree = tree.add(f"[bold cyan]{stmt.__class__.__name__}[/bold cyan]")
            build_rich_tree(stmt, child_tree)
            
    elif isinstance(node, Assign):
        target_tree = tree.add("[green]Target[/green]")
        build_rich_tree(node.target, target_tree.add(f"[bold cyan]{node.target.__class__.__name__}[/bold cyan]"))
        value_tree = tree.add("[yellow]Value[/yellow]")
        build_rich_tree(node.value, value_tree.add(f"[bold cyan]{node.value.__class__.__name__}[/bold cyan]"))
        
    elif isinstance(node, Identifier):
        tree.add(f"Name: [green]{node.name}[/green] (Line {node.line})")
        
    elif isinstance(node, MemberAccess):
        target_tree = tree.add("[green]Target[/green]")
        build_rich_tree(node.target, target_tree.add(f"[bold cyan]{node.target.__class__.__name__}[/bold cyan]"))
        tree.add(f"Member: [magenta].{node.member}()[/magenta] (Line {node.line})")
        
    elif isinstance(node, ArrayAccess):
        target_tree = tree.add("[green]Target[/green]")
        build_rich_tree(node.target, target_tree.add(f"[bold cyan]{node.target.__class__.__name__}[/bold cyan]"))
        index_tree = tree.add(f"[yellow]Index[/yellow] (Line {node.line})")
        build_rich_tree(node.index, index_tree.add(f"[bold cyan]{node.index.__class__.__name__}[/bold cyan]"))
        
    elif isinstance(node, Call):
        target_tree = tree.add("[green]Target[/green]")
        build_rich_tree(node.target, target_tree.add(f"[bold cyan]{node.target.__class__.__name__}[/bold cyan]"))
        if hasattr(node, 'args') and node.args:
            args_tree = tree.add("[yellow]Arguments[/yellow]")
            for arg in node.args:
                if arg:
                    build_rich_tree(arg, args_tree.add(f"[bold cyan]{arg.__class__.__name__}[/bold cyan]"))
                    
    elif isinstance(node, BinOp):
        left_tree = tree.add("[green]Left[/green]")
        build_rich_tree(node.left, left_tree.add(f"[bold cyan]{node.left.__class__.__name__}[/bold cyan]"))
        tree.add(f"Operator: [red]{node.op}[/red]")
        right_tree = tree.add("[yellow]Right[/yellow]")
        build_rich_tree(node.right, right_tree.add(f"[bold cyan]{node.right.__class__.__name__}[/bold cyan]"))
        
    elif isinstance(node, UnOp):
        tree.add(f"Operator: [red]{node.op}[/red]")
        right_tree = tree.add("[yellow]Right[/yellow]")
        build_rich_tree(node.right, right_tree.add(f"[bold cyan]{node.right.__class__.__name__}[/bold cyan]"))
        
    elif isinstance(node, Literal):
        if isinstance(node.val, list):
            elements_tree = tree.add("[yellow]Elements[/yellow]")
            for e in node.val:
                build_rich_tree(e, elements_tree.add(f"[bold cyan]{e.__class__.__name__}[/bold cyan]"))
        else:
            tree.add(f"Value: [magenta]{node.val}[/magenta]")
            
    return tree

# ============================================================================
# Main Execution
# ============================================================================

def lint_script(filepath: str, debug: bool = False) -> bool:
    """Reads, parses, and lints the target TegraScript file."""
    print(f"Linting: {os.path.basename(filepath)}")
    
    with open(filepath, 'r', encoding='utf-8') as file:
        text = file.read()
        
    lexer = Lexer(text)
    tokens = lexer.tokenize()
    
    parser = Parser(tokens)
    ast = parser.parse_program()
    
    if debug:
        if RICH_AVAILABLE:
            console = Console()
            console.print("\n[bold]AST Representation:[/bold]")
            tree_repr = build_rich_tree(ast)
            console.print(tree_repr)
            console.print("\n[bold]AST Validation:[/bold]")
        else:
            print("\nWarning: 'rich' module is not installed. Debug AST printing is disabled.")
            print("Install it with: pip install rich\n")
    
    linter = Linter()
    linter.collect_assignments(ast)
    linter.check(ast)
    linter.report_unused() # Report unused declarations after checking usage
    
    if linter.errors > 0:
        print("\nSemantic check FAILED.\n")
        return False
        
    print("\nSemantic check PASSED.\n")
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TegraScript AST Linter")
    parser.add_argument("script", help="Path to the .te script to lint")
    parser.add_argument("-d", "--debug", action="store_true", help="Print the generated AST using rich")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.script):
        print(f"Error: File not found: {args.script}")
        sys.exit(1)
        
    success = lint_script(args.script, args.debug)
    sys.exit(0 if success else 1)