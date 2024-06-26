import Parser
import lexer

from typing import Optional
from enum import Enum
import re
import os
import random
from collections import OrderedDict

import argparse

# We don't typically pass filenames through command line, this is mostly for batch compile operations.
filename_parser = argparse.ArgumentParser()
filename_parser.add_argument("--filename", help="Name of source file to be compiled.")
args = filename_parser.parse_args()

# source_file = "examples\\vector_source.c"
# source_file = "examples\\unique_ptr_source.c"
# source_file = "examples\\string_class_source.c"
# source_file = "examples\\initializer_list.c"
# source_file = "examples\\Reflection.c"
# source_file = "examples\\List.c"
# source_file = "examples\\constexpr_dict.c"
# source_file = "examples\\decorators_inside_fn_body.c"
# source_file = "examples\\enumerate_source.c"
# source_file = "examples\\parser_tests.c"

# source_file = "examples\\struct_source.c"
# source_file = "examples\\string_source.c"
# source_file = "examples\\string_split.c"
# source_file = "examples\\fileexample.c"
# source_file = "examples\\vector_of_strings.c"
# source_file = "examples\\function_example.c"

# source_file = "Bootstrap\\lexer_test.c"
source_file = "Bootstrap\\preprocess_test.c"

if args.filename:
    source_file = args.filename


output_file_name = source_file.split(".")[0] + "_generated.c"

file = open(source_file, "r")
Lines = file.readlines()
file.close()

imported_modules = []

for Line in Lines:
    Line = Line.strip()
    if Line.startswith("import "):
        # import String
        # import Vector
        # import UniquePtr
        _, module_name = Line.split()
        imported_modules.append(module_name)

if len(imported_modules) > 0:
    ImportedCodeLines = []
    for module_name in imported_modules:
        relative_path = "Lib\\" + module_name + ".c"
        module_file_path = os.path.join(os.getcwd(), relative_path)

        module_file = open(module_file_path, "r")
        lines = module_file.readlines()
        module_file.close()

        ImportedCodeLines += lines

    Lines = ImportedCodeLines + Lines


LinesCache = []

inside_match_loop = False
match_condition_count = 0
match_variable_name = ""
match_type = "variable"  # variable,struct
match_struct_type_info = []

struct_definations = []
instanced_struct_names = []
GlobalStructInitCode = ""

# Cached Items.
string_variable_names = []

# If any structs have __init__ method, then we register them here.
# {struct_type:parameters}
structs_with_constructors = {}

def has_constructors(p_struct_type: str) -> bool:
    return p_struct_type in structs_with_constructors.keys()

# UTILS BEGIN


# Insert a string at a given index in another string.
def insert_string(original_string, index, string_to_insert) -> str:
    return original_string[:index] + string_to_insert + original_string[index:]


def get_format_specifier(p_type: str) -> str:
    db = {"char": "c", "int": "d", "float": "f", "size_t": "llu"}

    is_array_type = p_type[0] == "[" and p_type[-1] == "]"
    if is_array_type:
        p_type = p_type[1:-1]

    if p_type in db.keys():
        return db[p_type]
    else:
        return "d"


def get_mangled_fn_name(p_struct_type: str, p_fn_name: str) -> str:
    return p_struct_type + p_fn_name


def get_templated_mangled_fn_name(
    p_struct_type: str, p_fn_name: str, p_templated_data_type: str
) -> str:
    return p_struct_type + "_" + p_templated_data_type + p_fn_name


# UTILS END


def get_destructor_for_struct(p_name):
    for struct in instanced_struct_names[::-1]:
        if struct.should_be_freed:
            # Maybe : Check for scope
            # Since variables can't be repeated across scopes,
            # We mayn't need to check for scopes.
            struct_name = struct.struct_name
            if struct_name == p_name:
                if struct.struct_type_has_destructor():
                    destructor_fn_name = struct.get_destructor_fn_name()
                    des_code = f"{destructor_fn_name}(&{struct_name});\n"
                    return des_code
    return None


class Symbol:
    def __init__(self, p_name, p_data_type):
        self.name = p_name
        self.data_type = p_data_type


class SymbolTable:
    def __init__(self):
        self.symbols = {}
        self.scope_stack = []

    def current_scope(self):
        return self.scope_stack[-1]

    def new_unique_scope_id(self):
        if len(self.scope_stack) == 0:
            # We haven't created any scopes.
            # So, the first scope created is 0.
            return 0

        latest_scope = self.current_scope()
        new_scope = latest_scope + 1

        if new_scope in self.scope_stack:
            while True:
                random_index = random.randrange(100000)
                if not (random_index in self.scope_stack):
                    new_scope = random_index
                    break

        return new_scope

    def enter_scope(self):
        new_scope_id = self.new_unique_scope_id()
        self.scope_stack.append(new_scope_id)
        self.symbols[new_scope_id] = OrderedDict()

    def exit_scope(self):
        if self.scope_stack:
            exiting_scope_id = self.scope_stack.pop()
            destructor_code = self.destructor_for_all_variables_in_scope(
                exiting_scope_id
            )
            if destructor_code != "":
                LinesCache.append(destructor_code)

    def print_symbol_table(self):
        print("-------------------Symbol Table------------------")
        for scope in self.scope_stack:
            for symbol in self.symbols[scope]:
                print(f"{scope} {symbol}")
        print("-------------------------------------------------")

    def declare_variable(self, name, p_type):
        current_scope = self.current_scope()

        if name in self.symbols[current_scope]:
            self.print_symbol_table()
            raise ValueError(f"Variable '{name}' already declared in this scope.")

        for scope in self.scope_stack:
            if name in self.symbols[scope]:
                self.print_symbol_table()
                raise ValueError(
                    f"Variable '{name}' already declared in previous scope {scope}."
                )

        self.symbols[current_scope][name] = Symbol(name, p_type)

    def find_variable(self, name):
        for scope in reversed(self.scope_stack):
            if name in self.symbols[scope]:
                return self.symbols[scope][name]
        return None

    def destructor_for_all_variables_in_scope(self, scope_id):
        # Return the destructor code for all variables in the provided scope
        # And, free(unregister) those variables as well.
        des_code = ""
        if scope_id in self.symbols:
            for variable in reversed(self.symbols[scope_id]):
                # Call the destructor for the variable
                # print(f"Destroying variable '{variable}' in scope {scope_id}")
                code = get_destructor_for_struct(variable)
                if code != None:
                    # print(f"~() = {code}")
                    des_code += code
            del self.symbols[scope_id]
        return des_code

    def destructor_code_for_all_remaining_variables(self):
        destructor_code = ""
        while True:
            if self.scope_stack:
                exiting_scope_id = self.scope_stack.pop()
                des_code = self.destructor_for_all_variables_in_scope(exiting_scope_id)
                if des_code != "":
                    destructor_code += des_code
            else:
                break
        return destructor_code


symbol_table = SymbolTable()
symbol_table.enter_scope()  # Create a new fresh scope.

# symbol_table.enter_scope()  # Enter Scope 1
# symbol_table.declare_variable("a", "str")
# symbol_table.declare_variable("b", "str")
# symbol_table.declare_variable("c", "str")

# symbol_table.enter_scope()  # Enter Scope 2
# symbol_table.declare_variable("d", "str")
# symbol_table.exit_scope()  # Exit Scope 2

# symbol_table.enter_scope()  # Enter Scope 3
# symbol_table.declare_variable("e", "str")
# symbol_table.exit_scope()  # Exit Scope 3

# symbol_table.exit_scope()  # Exit Scope 1


def get_current_scope():
    return symbol_table.current_scope()


def increment_scope():
    symbol_table.enter_scope()


def decrement_scope():
    symbol_table.exit_scope()


def REGISTER_VARIABLE(p_var_name: str, p_var_data_type: str) -> None:
    symbol_table.declare_variable(p_var_name, p_var_data_type)


def get_type_of_variable(p_var_name):
    var = symbol_table.find_variable(p_var_name)
    if var == None:
        return None
    else:
        return var.data_type


def is_variable_of_type(p_var_name, p_type):
    var_type = get_type_of_variable(p_var_name)
    if var_type == None:
        return False

    return var_type == p_type


def is_variable(p_var_name) -> bool:
    return get_type_of_variable(p_var_name) != None


def is_variable_char_type(p_var_name):
    return is_variable_of_type(p_var_name, "char")


def is_variable_string_class(p_var_name):
    struct_info = get_instanced_struct(p_var_name)
    if struct_info != None:
        return struct_info.struct_type == "String"
    return False


def is_variable_str_type(p_var_name):
    return is_variable_of_type(p_var_name, "str") or is_variable_of_type(
        p_var_name, "char*"
    )


def is_variable_boolean_type(p_var_name):
    return is_variable_of_type(p_var_name, "bool")


def is_variable_int_type(p_var_name):
    return is_variable_of_type(p_var_name, "int")


def is_variable_array_type(p_var_name):
    var_type = get_type_of_variable(p_var_name)
    if var_type == None:
        return False

    is_array_type = var_type[0] == "[" and var_type[-1] == "]"
    return is_array_type


def is_data_type_struct_object(p_data_type):
    struct_info = get_struct_defination_of_type(p_data_type)
    return struct_info != None


class NestingLevel(Enum):
    FOR_LOOP = 0
    IF_STATEMENT = 1
    ELSE_STATEMENT = 2


nesting_levels = []

# From where did the function body write started.
# We have to insert function hooks later here.
impl_body_start_pos = 0
currently_reading_fn_name = ""
currently_reading_fn_name_unmangled = ""
currently_reading_return_type = ""

currently_reading_fn_body = ""
currently_reading_fn_parent_struct = ""
currently_reading_parameters = []
should_write_fn_body = True

HOOKS_hook_fn_name = ""
HOOKS_target_fn = ""


class MemberDataType:
    def __init__(self, p_data_type, p_member, p_is_generic) -> None:
        self.data_type = p_data_type
        self.member = p_member
        self.is_generic = p_is_generic


class MemberFunction:
    def __init__(
        self, p_fn_name: str, p_fn_arguments: list, p_return_type: str
    ) -> None:
        self.fn_name = p_fn_name
        self.fn_arguments = p_fn_arguments
        self.fn_body = ""
        self.return_type = p_return_type

        self.is_overloaded_function = False
        self.overload_for_template_type = ""

    def is_destructor(self):
        return self.fn_name == "__del__"

    def print_info(self):
        print("--------------------------------------------------")
        print(f"Function Name : {self.fn_name}")
        print(f"Function Arguments : {self.fn_arguments}")
        print(f"Function Body : \n{self.fn_body}")
        print(f"Return Type: \n{self.return_type}")
        print("--------------------------------------------------")

# Functions which aren't defined inside structs.
GlobalFunctions = []

def is_global_function(p_fn_name: str) -> bool:
    return any(fn.fn_name == p_fn_name for fn in GlobalFunctions)

def get_global_function_by_name(p_fn_name: str):
    for fn in GlobalFunctions:
        if fn.fn_name == p_fn_name:
            return fn


class Struct:
    def __init__(self, p_struct_name: str, p_members: list) -> None:
        self.name = p_struct_name
        self.members = p_members
        self.member_functions = []
        self.macro_definations = []
        self.is_class_templated = False
        self.templated_data_type = ""
        self.template_defination_variable = ""

    def is_templated(self) -> bool:
        return self.is_class_templated

    def has_destructor(self) -> bool:
        return any(fn.is_destructor() for fn in self.member_functions)

    def has_member_fn(self, p_fn) -> bool:
        return any(fn.fn_name == p_fn for fn in self.member_functions)

    def has_macro(self, p_def_name) -> bool:
        return any(macro_def.name == p_def_name for macro_def in self.macro_definations)

    def get_return_type_of_fn(self, p_fn_name) -> str:
        for fn in self.member_functions:
            if fn.fn_name == p_fn_name:
                is_templated = self.is_templated() and (self.templated_data_type != "")

                if is_templated:
                    if fn.return_type == self.template_defination_variable:
                        return self.templated_data_type
                return fn.return_type

    def get_function_arguments(self, p_fn_name):
        for fn in self.member_functions:
            if fn.fn_name == p_fn_name:
                return fn.fn_arguments
        return None
        # raise Exception(f"Function {p_fn_name} not found.")

    def print_member_fn_info(self):
        for fn in self.member_functions:
            fn.print_info()

    def get_struct_initialization_code(self) -> str:
        struct_code = f"struct {self.name} {{\n"
        for struct_member in self.members:
            type = struct_member.data_type
            mem = struct_member.member
            is_generic = struct_member.is_generic
            struct_code += f"{type} {mem};\n"
        struct_code += f"}};\n\n"
        return struct_code

    def ensure_has_function(self, p_fn_name: str, p_instance_name=""):
        if not self.has_member_fn(p_fn_name):
            print("The Struct has following functions : ")
            self.print_member_fn_info()
            if p_instance_name != "":
                raise ValueError(
                    f"{self.name}({p_instance_name}) doesn't have the function name : {p_fn_name}"
                )
            else:
                raise ValueError(
                    f"{self.name} doesn't have the function name : {p_fn_name}"
                )


class StructInstance:

    def __init__(
        self,
        p_struct_type,
        p_struct_name,
        p_is_templated: bool,
        p_templated_data_type: str,
        p_scope,
    ) -> None:
        self.struct_type = p_struct_type
        self.struct_name = p_struct_name
        self.is_templated = p_is_templated
        self.templated_data_type = p_templated_data_type

        self.scope = p_scope
        # 'scope' denotes where this struct was created.
        self.should_be_freed = True
        # 'should_be_freed' is a tag wether the destructor should be called when it reaches out of the scope.
        # Things like Function parameters shouldn't be freed at the end of the scope.

    def is_templated_instance(self) -> bool:
        return self.is_templated

    def get_struct_defination(self) -> Optional[Struct]:
        StructInfo = get_struct_defination_of_type(self.struct_type)
        return StructInfo

    def get_fn_arguments(self, p_fn_name):
        fn_args = self.get_struct_defination().get_function_arguments(p_fn_name)
        if fn_args == None:
            raise Exception(f"Arguments for function {p_fn_name} not found.")
        return fn_args

    def struct_type_has_destructor(self) -> bool:
        return self.get_struct_defination().has_destructor()

    def _validate_template_type(self):
        if self.templated_data_type == "":
            # Shouldn't happen, as this validation is called if is_templated_instance() is true.
            raise Exception(
                "[Fatal Error] Struct is templated but it's type isn't Registered."
            )

    def get_mangled_function_name(self, p_fn_name: str) -> str:
        if self.is_templated_instance():
            self._validate_template_type()
            return get_templated_mangled_fn_name(
                self.struct_type, p_fn_name, self.templated_data_type
            )
        else:
            return get_mangled_fn_name(self.struct_type, p_fn_name)

    def get_destructor_fn_name(self) -> str:
        return self.get_mangled_function_name("__del__")


def get_struct_defination_of_type(p_struct_type: str) -> Optional[Struct]:
    for defined_struct in struct_definations:
        if defined_struct.name == p_struct_type:
            return defined_struct
    return None


def is_instanced_struct(p_struct_name: str):
    return any(struct.struct_name == p_struct_name for struct in instanced_struct_names)


def get_instanced_struct(p_struct_name) -> Optional[StructInstance]:
    for struct in instanced_struct_names:
        if struct.struct_name == p_struct_name:
            return struct
    return None


def get_struct_type_of_instanced_struct(p_struct_name):
    for struct in instanced_struct_names:
        if struct.struct_name == p_struct_name:
            return struct.struct_type


def add_fn_member_to_struct(p_struct_name: str, p_fn: MemberFunction):
    struct_defination = get_struct_defination_of_type(p_struct_name)
    if struct_defination is None:
        raise ValueError(f"Struct type {p_struct_name} doesn't exist.")
    else:
        struct_defination.member_functions.append(p_fn)


def add_fnbody_to_member_to_struct(p_struct_name: str, p_fn_name: str, p_fn_body: str):
    struct_defination = get_struct_defination_of_type(p_struct_name)
    if struct_defination is None:
        raise ValueError(f"Struct type {p_struct_name} doesn't exist.")
    else:
        # If not reversed, this will always find the first function and set the fn body to that.
        # because we have overloaded functions, all those functions have same name.
        # This function should set function body to the function which has been recently registered to the struct defination.
        for fn in reversed(struct_defination.member_functions):
            if fn.fn_name == p_fn_name:
                fn.fn_body = p_fn_body
                break


class ObjectInstance:
    def __init__(
        self, p_struct_name, p_is_templated, p_templated_data_type=None
    ) -> None:
        self.struct_name = p_struct_name
        self.is_templated = p_is_templated
        self.templated_data_type = p_templated_data_type


# This stores which structs are instanced, so to fix redefination error while generating C code.
ObjectInstances = []


def is_class_already_instantiated(p_struct_name, p_is_templated, p_templated_data_type):
    for ObjInstance in ObjectInstances:
        struct_name = ObjInstance.struct_name
        is_templated = ObjInstance.is_templated
        templated_data_type = ObjInstance.templated_data_type

        struct_name_matches = struct_name == p_struct_name
        template_matches = False

        # Both the template and the templated type matches.
        if is_templated and p_is_templated:
            if templated_data_type == p_templated_data_type:
                template_matches = True

        # Normal, untemplated classes.
        if (not is_templated) and (not p_is_templated):
            template_matches = True

        if struct_name_matches and template_matches:
            return True
    return False


class MacroDefination:
    def __init__(self, p_name, p_first_param, p_fn_body) -> None:
        self.name = p_name
        self.first_param = p_first_param
        self.fn_body = p_fn_body
        self.first_param_initializer_list = False


MacroDefinations = []


def is_macro_name(p_name: str) -> bool:
    return any(macro_def.name == p_name for macro_def in MacroDefinations)


def get_macro_def_by_name(p_name: str):
    for macro_def in MacroDefinations:
        if macro_def.name == p_name:
            return macro_def


def get_macro_def_by_name_of_class(p_macro_name: str, struct_type):
    StructInfo = get_struct_defination_of_type(struct_type)

    for macro_def in StructInfo.macro_definations:
        if macro_def.name == p_macro_name:
            return macro_def

    raise ValueError(f'{struct_type} has no macro names "{p_macro_name}".')


is_inside_def = False
currently_reading_def_name = ""
currently_reading_def_macro_type = ""  # NORMAL_MACRO,CLASS_MACRO
currently_reading_def_paramter_initializer_list = False
currently_reading_def_target_class = ""
currently_reading_def_parameter = ""
currently_reading_def_body = ""

is_inside_new_code = False
is_inside_struct_impl = False


temp_arr_length_variable_count = 0
temp_arr_search_variable_count = 0
temp_char_promoted_to_string_variable_count = 0

# This is to index which loop variable we are using for 'for loops'.
# like i,j,k...z,a..i
# First scope will use i.
# If we create another for within already created i loop, we now use j index and so on.
for_loop_depth = -1

# Constant Expressions Related Stuffs.

contexpr_functions = [
    "members_of",
    "member_functions_of",
    "instances_of_class",
]


class ConstexprDictionaryType:
    def __init__(self, p_dict_name: str, p_dictionary: dict) -> None:
        self.dict_name = p_dict_name
        self.dictionary = p_dictionary


constexpr_dictionaries = []


def is_constexpr_dictionary(p_dict_name) -> bool:
    return any(m_dict.dict_name == p_dict_name for m_dict in constexpr_dictionaries)


##############################################################################################
class HookInfo:
    # @hook_begin("custom_integer_printer" "int" data)
    #              ^ p_hook_fn_name              ^ p_hook_var_name
    #                                       ^ p_hook_fn_arg_type
    # The hook fn 'p_hook_fn_name' uses 'p_hook_var_name' of type 'p_hook_fn_arg_type'.
    def __init__(self, p_hook_fn_name, p_hook_fn_arg_type, p_hook_var_name) -> None:
        self.hook_fn_name = p_hook_fn_name
        self.hook_fn_arg_type = p_hook_fn_arg_type
        self.hook_var_name = p_hook_var_name


def parse_hook_info(p_hook_body) -> HookInfo:
    # @hook_begin("custom_integer_printer" "int" data)
    hook_parser = Parser.Parser(p_hook_body)

    hook_parser.consume_token(lexer.Token.AT)
    hook_parser.consume_token(lexer.Token.HOOK_BEGIN)
    hook_parser.consume_token(lexer.Token.LEFT_ROUND_BRACKET)

    hook_fn_name = hook_parser.extract_string_literal()
    hook_fn_arg_type = hook_parser.extract_string_literal()
    hook_var_name = hook_parser.get_token()

    hook_parser.consume_token(lexer.Token.RIGHT_ROUND_BRACKET)

    return HookInfo(hook_fn_name, hook_fn_arg_type, hook_var_name)


##############################################################################################


index = 0

while index < len(Lines):
    # Just strip away spaces and not other kinds of whitespaces, as that messes up final code generation.
    Line = Lines[index].strip(" ")
    index += 1

    if Line.startswith("// clang-format off"):
        continue
    elif Line.startswith("// clang-format on"):
        continue
    elif Line.startswith("import "):
        continue
    elif Line.startswith("///*///"):
        is_inside_new_code = not is_inside_new_code

    if not is_inside_new_code:
        # Normal C code, so just write that.
        LinesCache.append(Line)
        continue

    if is_inside_struct_impl and not "endfunc" in Line:
        currently_reading_fn_body += Line
        if should_write_fn_body:
            GlobalStructInitCode += Line
        continue
    elif is_inside_def and not "enddef" in Line:
        currently_reading_def_body += Line
        # Newlines are stripped while parsing 'Line'.
        # We add these newlines, so that 'currently_reading_def_body' can be splitted using newlines later.
        # That distinguishes unique lines.
        currently_reading_def_body += "\n"
        continue

    parser = Parser.Parser(Line)

    def check_token(token: lexer.Token):
        return parser.check_token(token)

    def create_string_iterator(array_name):
        global LinesCache
        LinesCache.append(
            f"char *iterator = {array_name};"
            f"while (*iterator != '\\0') {{"
            f"char value = *iterator;"
            f"iterator++;"
        )

    def create_normal_array_iterator(array_name, current_array_value_variable):
        global LinesCache

        # The variable type is in format '[int]'.
        array_type = get_type_of_variable(array_name)
        if array_type == None:
            raise Exception(f"{array_type} isn't a registered array type.")

        array_type = array_type[1:-1]

        LinesCache.append(
            f"for (unsigned int i = 0; i < {array_name}_array_size; i++){{\n"
            f"{array_type} {current_array_value_variable} = {array_name}[i];\n"
        )

        REGISTER_VARIABLE(current_array_value_variable, array_type)

    def create_array_iterator_from_struct(array_name, current_array_value_variable):
        instanced_struct_info = get_instanced_struct(array_name)
        if instanced_struct_info == None:
            # Shouldn't happen, as the caller function validates it before calling this function.
            raise Exception("Undefined struct.")

        StructInfo = instanced_struct_info.get_struct_defination()

        getter_fn = "__getitem__"
        len_fn = "len"

        fns_required_for_iteration = [getter_fn, len_fn]
        for fn in fns_required_for_iteration:
            StructInfo.ensure_has_function(fn, array_name)

        return_type = StructInfo.get_return_type_of_fn(getter_fn)

        getter_fn_name = instanced_struct_info.get_mangled_function_name(getter_fn)
        length_fn_name = instanced_struct_info.get_mangled_function_name(len_fn)

        if instanced_struct_info.is_templated_instance():
            # For Vector<char>, the return type is char.
            # This return type, is used for individual loop item.
            return_type = instanced_struct_info.templated_data_type

            if is_data_type_struct_object(return_type):
                # For Vector<String>, the String here is of struct type.
                # TODO : templated_data_type should probably be 'struct String' instead of just 'String' to avoid all this comparision.
                return_type = f"struct {return_type}"

        global temp_arr_length_variable_count
        global LinesCache

        global for_loop_depth

        loop_indices = [
            "i",
            "j",
            "k",
            "l",
            "m",
            "n",
            "o",
            "p",
            "q",
            "r",
            "s",
            "t",
            "u",
            "v",
            "w",
            "x",
            "y",
            "z",
            "a",
            "b",
            "c",
            "d",
            "e",
            "f",
            "g",
            "h",
        ]

        loop_counter_index = loop_indices[for_loop_depth % 26]

        temporary_var_name = f"tmp_len_{temp_arr_length_variable_count}"
        LinesCache.append(
            f"size_t {temporary_var_name} = {length_fn_name}(&{array_name});\n"
            f"for (size_t {loop_counter_index} = 0; {loop_counter_index} < {temporary_var_name}; {loop_counter_index}++){{\n"
            f"{return_type} {current_array_value_variable} = {getter_fn_name}(&{array_name}, {loop_counter_index});\n"
        )

        if "struct" in return_type:
            #               struct String
            # return type   ^^^^^^^^^^^^^
            # raw_return_type      ^^^^^^
            raw_return_type = return_type.split("struct")[1].strip()

            global instanced_struct_names
            instanced_struct_names.append(
                StructInstance(
                    raw_return_type,
                    current_array_value_variable,
                    False,
                    "",
                    get_current_scope(),
                )
            )

        REGISTER_VARIABLE(current_array_value_variable, return_type)
        # TODO : Important :: This should be freed after current scope.

        temp_arr_length_variable_count += 1

    def create_array_enumerator(
        array_name, ranged_index_item_variable, current_array_value_variable
    ):
        global LinesCache
        LinesCache.append(
            f"Iterator {array_name}_iter = create_iterator_from_array({array_name}, {array_name}_array_size); \n"
            f"Enumerator {array_name}_enumerator;\n"
            f"{array_name}_enumerator.index = -1;\n\n"
            f"while (enumerate(&{array_name}_iter, &{array_name}_enumerator)) {{\n"
            f"int {ranged_index_item_variable} = {array_name}_enumerator.index;\n"
            f"int {current_array_value_variable} = {array_name}_enumerator.val;\n"
        )

    def parse_data_type(inner=False):
        # inner is used as a tag for name mangling during recursive data parsing.

        # String,Vector<String>
        # This all should be parsed.
        # String
        # ^^^^^^
        data_type = parser.get_token()

        if data_type == "str":
            # str is an alias for char*, even though in most places we have checks for both "char*" and "str".
            data_type = "char*"

        data_type_str = data_type

        struct_defination = get_struct_defination_of_type(data_type)
        is_struct_type = struct_defination != None

        if is_struct_type:
            if not inner:
                data_type_str = f"struct {data_type_str}"

        # Vector<String>
        #       ^
        if parser.has_tokens_remaining() and parser.check_token(
            lexer.Token.SMALLER_THAN
        ):
            # This has_tokens_remaining() could probably integrated inside check_token() maybe.
            parser.consume_token(lexer.Token.SMALLER_THAN)
            #      Vector<Vector<String>>
            #             ^^^^^^^^^^^^^^
            # Hope, this case is recursively parsed as well.

            # TODO:Since, we don't immediately instantiate templates structs.They aren't registered here.
            # So, these checks fail, but these checks are important .
            """
            # Only a templated struct can be used to create a data type.
            if not is_struct_type:
                raise ValueError(f"Struct type {data_type} doesn't exist.")
                
            if not struct_defination.is_templated():
                raise Exception(
                    f"Struct type {data_type} isn't a template class and can't be used to instantiate new data types."
                )
            """

            inner_data_type = parse_data_type(inner=True)
            data_type_str += f"_{inner_data_type}"
            parser.consume_token(lexer.Token.GREATER_THAN)

            instantiate_template(data_type, inner_data_type)

        return data_type_str

    def parse_number():
        # COLOR["Red"]
        # ^^^^^^^^^^^^ Constexpr Dictionary.
        # 10
        # ^^ Number.
        # line.len
        #      ^^^ function returning a number(int).

        # TODO : Functions and struct member access still not supported.
        # TODO : Use this function more consistently in various parts of the parser.

        expr1 = parser.get_token()

        if is_constexpr_dictionary(expr1):
            return parse_constexpr_dictionary(expr1)
        elif is_variable_int_type(expr1):
            return expr1
        else:
            if not expr1.isdigit():
                raise ValueError(f"{expr1} isnot a number.")
            return expr1

    def parse_let(type_name, array_name):
        parser.consume_token(lexer.Token.EQUALS)
        parser.consume_token(lexer.Token.LEFT_SQUARE_BRACKET)

        array_values = []

        while parser.current_token() != lexer.Token.RIGHT_SQUARE_BRACKET:
            if type_name == "int":
                arr_value = parse_number()
            else:
                arr_value = parser.get_token()
            array_values.append(arr_value)
            # print(f"Array Value : {arr_value}")
            if parser.check_token(lexer.Token.COMMA):
                parser.next_token()

        parser.next_token()
        parser.consume_token(lexer.Token.SEMICOLON)

        array_element_count = len(array_values)
        array_elements_str = ",".join(array_values)

        global LinesCache
        LinesCache.append(f"{type_name} {array_name}[] = {{ {array_elements_str} }};\n")
        LinesCache.append(
            f"unsigned int {array_name}_array_size = {array_element_count};\n\n"
        )

        # register variable as '[int]' to indicate the array of type int.
        REGISTER_VARIABLE(array_name, f"[{type_name}]")

    def instantiate_template(struct_type, templated_data_type):
        # Immediately instantiate templated member functions.

        StructInfo = get_struct_defination_of_type(struct_type)
        if StructInfo == None:
            return

        global GlobalStructInitCode

        # Recreate Generic Structs on instantiation.
        m_struct_name = f"{struct_type}_{templated_data_type}"
        m_struct_info = get_struct_defination_of_type(m_struct_name)
        if m_struct_info != None:
            # This struct has already been defined.
            # So, we don't need to instantiate again.
            return

        struct_code = f"struct {m_struct_name}  {{\n"

        struct_members_list = StructInfo.members
        for struct_member in struct_members_list:
            type = struct_member.data_type
            mem = struct_member.member
            is_generic = struct_member.is_generic
            if is_generic:
                if "*" in type:
                    actual_type, star = type.split("*")
                    type = templated_data_type + "*"
                else:
                    type = templated_data_type

                if is_data_type_struct_object(templated_data_type):
                    type = f"struct {type}"

            struct_code += f"{type} {mem};\n"
        struct_code += f"}};\n\n"

        # Register this templated struct in order to insantiate the same generic type in the future.
        struct_data = Struct(m_struct_name, struct_members_list)
        # struct_data.is_class_templated = True
        # if is_data_type_struct_object(templated_data_type):
        #     struct_data.templated_data_type = f"struct {templated_data_type}"

        GlobalStructInitCode += struct_code

        templated_fn_code = f"//template {struct_type}<{templated_data_type}> {{\n"

        if StructInfo != None:
            defined_struct = StructInfo
            for fn in defined_struct.member_functions:
                parameters = fn.fn_arguments
                fn_name = fn.fn_name
                return_type = fn.return_type

                # This is just a raw unmodified copy, we need this to recreate the function defination, otherwise these parameters are modified below.
                parameters_copy = parameters

                if fn.is_overloaded_function:
                    # print("======================================")
                    # print(f"For struct {defined_struct.name}")
                    # print(
                    # f"fn {fn_name} is overloaded for class {fn.overload_for_template_type}"
                    # )

                    use_this_overload = False
                    if fn.overload_for_template_type == templated_data_type:
                        # This current fn is the suitable overload.
                        use_this_overload = True
                    else:
                        has_suitable_overload = False
                        for m_fn in defined_struct.member_functions:
                            if m_fn.fn_name != fn_name:
                                continue

                            if m_fn.overload_for_template_type == templated_data_type:
                                has_suitable_overload = True

                        if has_suitable_overload:
                            use_this_overload = False
                        else:
                            # TODO: We don't warn if there is no base overload.
                            if fn.overload_for_template_type == "#BASE#":
                                # No other suitable overloads found,
                                # and this fn is the base overload.
                                # Use this current fn to overload.
                                use_this_overload = True

                    if not use_this_overload:
                        continue

                # resolve templated fn params.
                if defined_struct.is_templated():
                    # print(f"Template type {templated_data_type}")

                    params_copy = []
                    for param in parameters:
                        data_type = param.data_type
                        param_name = param.member
                        if data_type == defined_struct.template_defination_variable:
                            data_type = templated_data_type
                            if is_data_type_struct_object(data_type):
                                data_type = "struct " + data_type

                        params_copy.append(data_type + " " + param_name)
                    parameters = params_copy

                    if return_type == defined_struct.template_defination_variable:
                        return_type = templated_data_type
                        if is_data_type_struct_object(return_type):
                            return_type = "struct " + return_type

                    # Register this function, but if it is templated, resolved the templated type and write the function.
                    m_fn = MemberFunction(fn_name, parameters_copy, return_type)
                    # m_fn.is_overloaded_function = is_overloaded_fn
                    # m_fn.overload_for_template_type = overload_for_type
                    struct_data.member_functions.append(m_fn)
                else:
                    struct_data.member_functions.append(fn)

                templated_struct_name = defined_struct.name + f"_{templated_data_type}"

                fn_name = get_templated_mangled_fn_name(
                    defined_struct.name, fn_name, templated_data_type
                )

                if len(parameters) > 0:
                    parameters_str = ",".join(parameters)
                    templated_fn_code += f"{return_type} {fn_name}(struct {templated_struct_name} *this, {parameters_str}) {{\n"
                else:
                    templated_fn_code += f"{return_type} {fn_name}(struct {templated_struct_name} *this) {{\n"
                templated_fn_code += f"{fn.fn_body} }}\n\n"
            templated_fn_code += (
                f"//template {struct_type}<{templated_data_type}> }}\n\n"
            )

            # if we want to use template type in fn body, we use following syntax.
            # @TEMPLATED_DATA_TYPE@
            if "@TEMPLATED_DATA_TYPE@" in templated_fn_code:
                m_templated_data_type = templated_data_type
                if is_data_type_struct_object(templated_data_type):
                    m_templated_data_type = "struct " + m_templated_data_type

                templated_fn_code = templated_fn_code.replace(
                    "@TEMPLATED_DATA_TYPE@", m_templated_data_type
                )

            GlobalStructInitCode += templated_fn_code

            global struct_definations
            struct_definations.append(struct_data)

    def parse_create_struct(struct_type, struct_name):
        global LinesCache

        is_templated_struct = False
        templated_data_type = ""

        StructInfo = get_struct_defination_of_type(struct_type)
        if StructInfo is None:
            raise ValueError(f'Struct type "{struct_type}" undefined.')

        if parser.check_token(lexer.Token.SMALLER_THAN):
            parser.next_token()
            templated_data_type = parser.current_token()
            # print(f"Templated struct of data type : {templated_data_type}")
            struct_members_list = StructInfo.members
            # print(StructInfo.members)  # [['X', 'a', True], ['float', 'b', False]]

            # GlobalStructInitCode += struct_code

            is_templated_struct = True

            parser.next_token()

            parser.consume_token(lexer.Token.GREATER_THAN)

        instanced_struct_info = StructInstance(
            struct_type,
            struct_name,
            is_templated_struct,
            templated_data_type,
            get_current_scope(),
        )

        global instanced_struct_names
        instanced_struct_names.append(instanced_struct_info)

        REGISTER_VARIABLE(struct_name, struct_type)

        class_already_instantiated = is_class_already_instantiated(
            struct_type, is_templated_struct, templated_data_type
        )

        if not class_already_instantiated:
            # GlobalStructInitCode += struct_code
            global ObjectInstances
            ObjectInstances.append(
                ObjectInstance(struct_type, is_templated_struct, templated_data_type)
            )

        parser.consume_token(lexer.Token.LEFT_CURLY)

        values_list = []

        while parser.current_token() != lexer.Token.RIGHT_CURLY:
            # Obtained String.
            string = ""
            parsed_string = False

            if parser.check_token(lexer.Token.QUOTE):
                string = parser.extract_string_literal()
                parsed_string = True

            if parsed_string:
                arr_value = '"' + string + '"'
            else:
                arr_value = parser.get_token()
            values_list.append(arr_value)
            # print(f"Array Value : {arr_value}")
            if parser.check_token(lexer.Token.COMMA):
                parser.next_token()

        parser.next_token()
        parser.match_token(lexer.Token.SEMICOLON)

        code = ""

        if is_templated_struct:
            code += f"struct {struct_type}_{templated_data_type} {struct_name};\n"
        else:
            code += f"struct {struct_type} {struct_name};\n"

        global GlobalStructInitCode

        # Immediately instantiate templated member functions.
        if is_templated_struct and (not class_already_instantiated):
            instantiate_template(struct_type, templated_data_type)

        has_constuctor = has_constructors(struct_type)

        if has_constuctor:
            values_str = ""
            if len(values_list) > 0:
                values_str = ",".join(values_list)

            LinesCache.append(code)
            constructor_CPL_code = f"{struct_name}.__init__({values_str})\n"

            index_to_insert_at = index
            Lines.insert(index_to_insert_at, constructor_CPL_code)
            return
        else:
            struct_var_values_pairs = list(zip(StructInfo.members, values_list))
            for struct_member, values in struct_var_values_pairs:
                # type = struct_member[0]
                mem = struct_member.member

                code += f"{struct_name}.{mem} = {values};\n"

        LinesCache.append(code)

    class ParameterType(Enum):
        UNDEFINED = -1
        RAW_STRING = 0
        CHAR_TYPE = 1
        STR_TYPE = 2
        STRING_CLASS = 3
        NUMBER = 4
        VARIABLE = 5

    class Parameter:
        def __init__(self, p_param, p_param_type: ParameterType) -> None:
            self.param = p_param
            self.param_type = p_param_type

    def _read_a_parameter():
        # Parse a number or string..
        # In any case, just a single symbol.
        parameter = None
        parameter_type = ParameterType.UNDEFINED

        tk = parser.current_token()
        if tk == lexer.Token.QUOTE:
            parameter = parser.extract_string_literal()
            parameter_type = ParameterType.RAW_STRING
        elif is_variable(tk):
            parameter = tk
            if is_variable_str_type(tk):
                parameter_type = ParameterType.STR_TYPE
            elif is_variable_string_class(tk):
                parameter_type = ParameterType.STRING_CLASS
            elif is_variable_char_type(tk):
                parameter_type = ParameterType.CHAR_TYPE
            else:
                parameter_type = ParameterType.VARIABLE
            parser.next_token()
        else:
            parameter = parse_number()
            parameter_type = ParameterType.NUMBER

        return Parameter(parameter, parameter_type)

    def _read_parameters_within_round_brackets():
        # Parse n number of parameters within two round brackets.
        # (param1)
        # (param1,param2...paramN)
        parameters = []

        parser.consume_token(lexer.Token.LEFT_ROUND_BRACKET)
        while parser.has_tokens_remaining():
            curr_param = parser.current_token()
            if curr_param == lexer.Token.RIGHT_ROUND_BRACKET:
                break

            parameters.append(_read_a_parameter())

            curr_param = parser.current_token()
            if curr_param != lexer.Token.RIGHT_ROUND_BRACKET:
                # There are few other tokens rather than ).
                # That should be a comma to indicate next parameter.
                parser.consume_token(lexer.Token.COMMA)
        parser.consume_token(lexer.Token.RIGHT_ROUND_BRACKET)

        return parameters

    class ParsedFunctionCallType(Enum):
        # To denote x = A.fn();
        STRUCT_FUNCTION_CALL = 0
        # To denote x = fn();
        GLOBAL_FUNCTION_CALL = 1

    def _parse_function_call(target, assignment_fn_call=True):
        # let str = str2[0]
        #     ^^^   ^^^^ ^
        #     |     |    |
        #     |     |    .________ parameters
        #     |     ._____________ target
        #     .___________________ varname

        # assignment_fn_call to hint its let X = A.Y();
        # And not, just a function call like A.X();

        instanced_struct_info = None
        StructInfo = None
        fn_name_unmangled = ""
        
        parsing_fn_call_type = ParsedFunctionCallType.STRUCT_FUNCTION_CALL

        if is_global_function(target):
            #let mangled_name = get_mangled_fn_name(class_name, fn_name);
            #                   ^^^^^^^^^^^^^^^^^^^ target
            parsing_fn_call_type = ParsedFunctionCallType.GLOBAL_FUNCTION_CALL
        else:
            instanced_struct_info = get_instanced_struct(target)
            if instanced_struct_info != None:
                parsing_fn_call_type = ParsedFunctionCallType.STRUCT_FUNCTION_CALL
                StructInfo = instanced_struct_info.get_struct_defination()
            else:
                raise ValueError(f"{target} is neither a struct function call nor a global function call.")
        
        # Parameters provided to a given function call.
        # let str = stra.__contains__(strb)
        #                             ^^^^ parameters
        # For indexed member acess:
        # let str = str2[0]
        #                ^  parameters
        parameters = []
        fn_args = []
        return_type = ""
        fn_name =  ""
        args = None

        if parsing_fn_call_type == ParsedFunctionCallType.STRUCT_FUNCTION_CALL:
            if parser.check_token(lexer.Token.LEFT_SQUARE_BRACKET):
                # For Dictionary Like items member acess.
                # let test = TOKEN_MAP["Option"]

                # let str = str2[0]
                #               ^
                fn_name_unmangled = "__getitem__"
                parser.consume_token(lexer.Token.LEFT_SQUARE_BRACKET)
                parameters = [_read_a_parameter()]
                parser.consume_token(lexer.Token.RIGHT_SQUARE_BRACKET)

                # [BEGIN] Automatic Conversion for String class
                is_parameter_string_object = False
                param_struct_info = get_instanced_struct(parameters[0].param)
                if param_struct_info != None:
                    is_parameter_string_object = param_struct_info.struct_type == "String"

                if is_parameter_string_object:
                    get_item_fn_args = instanced_struct_info.get_fn_arguments("__getitem__")
                    # Get the data type for the first function argument.
                    param_type = get_item_fn_args[0].data_type
                    expects_string_argument = param_type == "char*" or param_type == "str"

                    if expects_string_argument:
                        c_str_fn_name = param_struct_info.get_mangled_function_name("c_str")
                        # Recreate the obtained parameter as,
                        # Dictionary__getitem__(&CHARACTER_TOKENS, token)
                        #                                          ^^^^^
                        # to
                        # Dictionary__getitem__(&CHARACTER_TOKENS, Stringc_str(&token))
                        #                                          ^^^^^^^^^^^^^^^^^^^
                        m_param = f"{c_str_fn_name}(&{parameters[0].param})"
                        m_param_type = ParameterType.VARIABLE
                        #                            ^^^^^^^^^ Just a placeholder,nothing special.
                        param = Parameter(m_param, m_param_type)
                        parameters = [param]
                # [END] Automatic Conversion for String class
            elif parser.check_token(lexer.Token.DOT):
                # let str3 = str2.strip()
                #                 ^^^^^   fn_name_unmangled
                parser.next_token()
                fn_name_unmangled = parser.get_token()
                parameters = _read_parameters_within_round_brackets()
            else:
                raise Exception("Unimplemented Let Case.")

            StructInfo.ensure_has_function(fn_name_unmangled, target)

            args = StructInfo.get_function_arguments(fn_name_unmangled)
            return_type = StructInfo.get_return_type_of_fn(fn_name_unmangled)
            fn_name = instanced_struct_info.get_mangled_function_name(fn_name_unmangled)
        elif parsing_fn_call_type == ParsedFunctionCallType.GLOBAL_FUNCTION_CALL:
            fn_name = target #No need to mangle global functions.
            
            m_fn = get_global_function_by_name(target)

            args = m_fn.fn_arguments
            return_type = m_fn.return_type
            parameters = _read_parameters_within_round_brackets()

        if args != None:
            fn_args = [arg.data_type for arg in args]

        if len(fn_args) != len(parameters):
            raise ValueError(
                f'Expected {len(fn_args)} arguments for function "{fn_name}" but provided {len(parameters)} arguments.'
            )

        parameters_quoted = []
        for arg, parameter in zip(fn_args, parameters):
            param = parameter.param
            param_type = parameter.param_type

            if param_type == ParameterType.RAW_STRING:
                if (arg == "char*") or (arg == "str"):
                    param = f'"{param}"'
                else:
                    param = f"'{param}'"
                parameters_quoted.append(Parameter(param, param_type))
            else:
                parameters_quoted.append(parameter)
        parameters = parameters_quoted

        # Promotion of char to char* when char is provided to a function that expects a char*.
        char_to_string_promotion_code = ""
        for i, (arg, parameter) in enumerate(zip(fn_args, parameters)):
            param = parameter.param
            param_type = parameter.param_type

            expects_string_argument = (arg == "char*") or (arg == "str")
            if expects_string_argument:
                if param_type == ParameterType.CHAR_TYPE:
                    global temp_char_promoted_to_string_variable_count
                    promoted_char_var_name = f"{param}_promoted_{temp_char_promoted_to_string_variable_count}"
                    # Create a string from char.
                    promoted_char_var_code = (
                        f"char {promoted_char_var_name}[2] = {{ {param}, '\\0'}};"
                    )
                    REGISTER_VARIABLE(f"{promoted_char_var_name}", "str")

                    char_to_string_promotion_code += promoted_char_var_code
                    temp_char_promoted_to_string_variable_count += 1

                    # This new string replaces the old char param.
                    parameters[i].param = promoted_char_var_name
                elif param_type == ParameterType.STRING_CLASS:
                    param_struct_info = get_instanced_struct(param)
                    c_str_fn_name = param_struct_info.get_mangled_function_name("c_str")
                    # A function expects a string, & we provided a String class object.
                    # So, we perform getting string from String Class object.
                    # var -> Stringc_str(&var)
                    # ^^^ String Object.
                    parameters[i].param = f"{c_str_fn_name}(&{parameters[i].param})"

        if char_to_string_promotion_code != "":
            global LinesCache
            LinesCache.append(f"{char_to_string_promotion_code}\n")

        if assignment_fn_call:
            if "struct" in return_type:
                #              "struct Vector__String"
                # return type   ^^^^^^^^^^^^^^^^^^^^^
                # raw_return_type      ^^^^^^^^^^^^^^
                raw_return_type = return_type.split("struct")[1].strip()

                global instanced_struct_names
                instanced_struct_names.append(
                    StructInstance(
                        raw_return_type, var_name, False, "", get_current_scope()
                    )
                )

                REGISTER_VARIABLE(var_name, return_type)
            else:
                REGISTER_VARIABLE(var_name, return_type)

        parameters_str = ""
        has_parameters = len(parameters) > 0
        if has_parameters:
            params = [parameter.param for parameter in parameters]
            parameters_str = ",".join(params)

        return {
            "fn_name": fn_name,
            "return_type": return_type,
            "has_parameters": has_parameters,
            "parameters_str": parameters_str,
            "function_call_type" : parsing_fn_call_type
        }

    def parse_access_struct_member(var_name, target):
        # let str = str2[0]
        #     ^^^   ^^^^ ^
        #     |     |    |
        #     |     |    .________ parameters
        #     |     ._____________ target
        #     .___________________ varname

        parse_result = _parse_function_call(target)
        fn_name = parse_result["fn_name"]
        return_type = parse_result["return_type"]
        has_parameters = parse_result["has_parameters"]
        parsed_fn_call_type = parse_result["function_call_type"]

        assignment_code = f"{return_type} {var_name} = {fn_name}("

        if parsed_fn_call_type == ParsedFunctionCallType.STRUCT_FUNCTION_CALL:
            # Pass a 'this' parameter.
            assignment_code += f"&{target}"
            if has_parameters:
                assignment_code += ","

        if has_parameters:
            parameters_str = parse_result["parameters_str"]
            assignment_code += f"{parameters_str}"
        assignment_code += ");"

        global LinesCache
        LinesCache.append(f"{assignment_code}\n")

    def parse_macro(parsed_member, p_macro_type, p_macro_fn_name=""):
        # p_macro_type -> NORMAL_MACRO | CLASS_MACRO

        # For normal macros:
        # add_token_raw "="
        # ^^^^^^^^^^^^^ parsed_member

        # For class macros:
        # values.pushn 40 50 60
        #        ^^^^^ p_macro_fn_name
        # ^^^^^^ parsed_member

        macro_def = None

        if p_macro_type == "NORMAL_MACRO":
            macro_def = get_macro_def_by_name(parsed_member)
        elif p_macro_type == "CLASS_MACRO":
            struct_type = get_struct_type_of_instanced_struct(parsed_member)
            macro_def = get_macro_def_by_name_of_class(p_macro_fn_name, struct_type)

        macro_has_parameter = macro_def.first_param != ""

        # add_token_raw "="
        parser.next_token()

        # Get the remaining tokens
        remaining_tokens = ""
        if p_macro_type == "NORMAL_MACRO":
            remaining_tokens = Line.split(parsed_member)[1]
        elif p_macro_type == "CLASS_MACRO":
            remaining_tokens = Line.split(p_macro_fn_name)[1]

        remaining_tokens = remaining_tokens.strip()

        # Remove any spaces in between the supplied arguments.
        # FIXME : If we have used the Parser to parse, we didn't need to do such workarounds.
        params_uncleaned = remaining_tokens
        params_uncleaned = params_uncleaned.split(" ")

        # Keep only the parameters, which are not (just spaces or 'n' number of spaces which may have appeared during splitting process).
        params = [param for param in params_uncleaned if param.strip() != ""]
        parameters_provided = len(params) > 0

        if macro_has_parameter:
            if not parameters_provided:
                raise ValueError(
                    f'"{parsed_member}" macro expects an argument but provided None.'
                )
        else:
            if parameters_provided:
                raise ValueError(
                    f'"{parsed_member}" macro doesn\'t expect an argument but provided {len(params)} parameters.'
                )

        # Write the macro name by replacing the fn parameter.
        code = ""

        code = macro_def.fn_body

        if p_macro_type == "CLASS_MACRO":
            # def pushn:
            #   self.push ..
            # a.pushn .. -> a.push ..
            code = code.replace("self", parsed_member)

        # X.size for init_list
        if macro_def.first_param_initializer_list:
            # def f X...
            # X.size -> this is replaced by the number of arguments passed to the macro call.
            # end def
            size_str = f"{macro_def.first_param}.size"
            if size_str in code:
                # print(params)
                length = str(len(params))
                code = code.replace(size_str, length)

        if macro_has_parameter:
            code = code.replace(macro_def.first_param, remaining_tokens)
        code_list = code.split("\n")
        # print(code_list)

        new_code = []
        for line in code_list:
            if "forall" in line:
                # forall x: values.push_unchecked x
                #       ^ rhs----------------------
                rhs = line.split("forall")[1]
                split = rhs.split(":")
                forall_param = split[0].strip()
                forall_code = split[1].strip()

                # forall x:
                #        ^ (forall_param)

                is_constexpr_function = any(
                    constexpr_func in forall_param
                    for constexpr_func in contexpr_functions
                )

                parameters_to_replace = params

                if is_constexpr_function:
                    # forall x in members_of(Vector)
                    #        ^forall_param
                    classname = forall_param[
                        forall_param.find("(") + 1 : forall_param.find(")")
                    ]

                    forall_param_unmodified = forall_param

                    forall_param = forall_param[0 : forall_param.find("in")]
                    forall_param = forall_param.strip()

                    struct_def = get_struct_defination_of_type(classname)
                    if struct_def == None:
                        raise ValueError(f"{classname} class isn't registered.")

                    # These constexpr functions returns "strings", which maybe toggled by an UNQUOTE optional parameter to get the raw objects like the struct names.
                    unquote_location = forall_param_unmodified.find("UNQUOTE")
                    has_unquote_text = unquote_location != -1
                    if has_unquote_text:
                        right_bracket_location = forall_param_unmodified.find(")")
                        if (
                            "UNQUOTE"
                            in forall_param_unmodified[right_bracket_location:]
                        ):
                            has_unquote_text = True
                        else:
                            has_unquote_text = False

                    def stringify(p_str):
                        if has_unquote_text:
                            return p_str
                        else:
                            return '"' + p_str + '"'

                    fn_names = []

                    if "members_of" in forall_param_unmodified:
                        fn_names = [
                            stringify(member.member) for member in struct_def.members
                        ]
                    elif "member_functions_of" in forall_param_unmodified:
                        fn_names = [
                            stringify(fn.fn_name) for fn in struct_def.member_functions
                        ]
                    elif "instances_of_class" in forall_param_unmodified:
                        fn_names = [
                            stringify(struct.struct_name)
                            for struct in instanced_struct_names
                            if struct.struct_type == classname
                        ]
                    else:
                        print(
                            f"Only the following constexpr functions are allowed : {contexpr_functions}"
                        )
                        raise ValueError("Constexpr function undefined.")

                    parameters_to_replace = fn_names

                forall_code_combined = ""
                for param in parameters_to_replace:
                    replaced_code = forall_code.replace(forall_param, param)
                    forall_code_combined = replaced_code + "\n"
                    new_code.append(forall_code_combined)
            else:
                new_code.append(line)

        index_to_insert_at = index
        global Lines
        Lines = Lines[:index_to_insert_at] + new_code + Lines[index_to_insert_at:]

    def parse_constexpr_dictionary(p_dict_name):
        parser.consume_token(lexer.Token.LEFT_SQUARE_BRACKET)
        key = parser.extract_string_literal()
        parser.consume_token(lexer.Token.RIGHT_SQUARE_BRACKET)

        for dict in constexpr_dictionaries:
            if dict.dict_name == p_dict_name:
                try:
                    return dict.dictionary[key]
                except KeyError:
                    print(
                        f'[Error] Key "{key}" wasn\'t found in the constexpr dictionary {p_dict_name}.'
                    )
        # Shouldn't happen as the caller functions have already verified the presence of the dictionaries.
        raise ValueError(f"Constexpr dictionary {p_dict_name} is undefined.")

    if is_inside_def and "enddef" in Line:
        if check_token(lexer.Token.ENDDEF):
            is_inside_def = False

            macro_def = MacroDefination(
                currently_reading_def_name,
                currently_reading_def_parameter,
                currently_reading_def_body,
            )
            macro_def.first_param_initializer_list = (
                currently_reading_def_paramter_initializer_list
            )

            if currently_reading_def_macro_type == "NORMAL_MACRO":
                MacroDefinations.append(macro_def)
            elif currently_reading_def_macro_type == "CLASS_MACRO":
                struct_defination = get_struct_defination_of_type(
                    currently_reading_def_target_class
                )
                if struct_defination is None:
                    raise ValueError(
                        f"Struct type {currently_reading_def_target_class} doesn't exist."
                    )
                else:
                    struct_defination.macro_definations.append(macro_def)

            currently_reading_def_name = ""
            currently_reading_def_parameter = ""
            currently_reading_def_macro_type = "NORMAL_MACRO"
            currently_reading_def_target_class = ""
            currently_reading_def_paramter_initializer_list = False
            currently_reading_def_body = ""
        continue

    def parse_term():
        """Parse a variable(a single term)"""

        # TODO: _read_a_parameter() morever has this functionality, so maybe use that.

        global parser

        term_type = "UNDEFINED"
        variable = ""
        output_code = ""

        if parser.check_token(lexer.Token.QUOTE):
            variable = parser.extract_string_literal()
            term_type = "string_literal"
        else:
            variable = parser.get_token()

            if is_variable_boolean_type(variable):
                term_type = "bool"
            elif is_variable_char_type(variable):
                term_type = "char"
            else:
                instanced_struct_info = get_instanced_struct(variable)
                if instanced_struct_info != None:
                    # Struct type.
                    # if Line.startswith("import"){
                    if parser.check_token(lexer.Token.DOT):
                        # parser.next_token()

                        parser = Parser.Parser(Line)
                        # if Line.startswith("import"){
                        parser.next_token()
                        parser.next_token()

                        parse_result = _parse_function_call(variable, False)
                        fn_name = parse_result["fn_name"]
                        return_type = parse_result["return_type"]

                        if parse_result["has_parameters"]:
                            parameters_str = parse_result["parameters_str"]
                            output_code = f"{fn_name}(&{variable}, {parameters_str})"
                        else:
                            output_code = f"{fn_name}(&{variable})"
                        term_type = "struct_function_call"
                    else:
                        term_type = "struct"
                else:
                    # TODO: Maybe use parse_number() ???
                    term_type = "variable"

        if output_code == "":
            output_code = variable

        return {
            "type": term_type,
            "output_code": output_code,
            "variable_name": variable,
        }

    def get_comparision_operator():
        """Read comparision operator (if any) which includes >, <, >=, <=, ==, !=, in"""
        has_comparision_operator = False
        operators_as_str = ""

        global parser

        if parser.has_tokens_remaining():
            token_map = {
                lexer.Token.GREATER_THAN: ">",
                lexer.Token.SMALLER_THAN: "<",
                lexer.Token.EQUALS: "=",
                lexer.Token.EXCLAMATION: "!",
            }

            token = parser.current_token()

            if token in token_map:
                parser.consume_token(token)

                has_comparision_operator = True
                operators_as_str = token_map[token]

                if (
                    parser.has_tokens_remaining()
                    and parser.current_token() == lexer.Token.EQUALS
                ):
                    parser.consume_token(lexer.Token.EQUALS)
                    operators_as_str += "="
            elif token == lexer.Token.IN:
                parser.consume_token(lexer.Token.IN)

                has_comparision_operator = True
                operators_as_str = "in"

        return {
            "has_comparision_operator": has_comparision_operator,
            "operators_as_str": operators_as_str,
        }

    def boolean_expression():
        lhs = parse_term()

        comparision_operation = get_comparision_operator()
        if comparision_operation["has_comparision_operator"]:
            operators_as_str = comparision_operation["operators_as_str"]

            rhs = parse_term()

            global parser
            parser.consume_token(lexer.Token.LEFT_CURLY)

            if operators_as_str == "==" or operators_as_str == "!=":
                negation_boolean_expression = operators_as_str == "!="

                var_to_check_against = lhs["variable_name"]
                var_to_check = rhs["variable_name"]

                if rhs["type"] == "string_literal":
                    comparision_code = ""

                    if lhs["type"] == "struct":
                        # if str == "Hello"
                        #           ^^^^^^^ String Literal
                        # if str != "Hello"
                        #    ^^^ String struct
                        eq_fn = "__eq__"
                        fns_required_for_equality = [eq_fn]

                        instanced_struct_info = get_instanced_struct(
                            var_to_check_against
                        )

                        struct_type = instanced_struct_info.struct_type
                        StructInfo = instanced_struct_info.get_struct_defination()

                        if struct_type == "String":
                            for fn in fns_required_for_equality:
                                StructInfo.ensure_has_function(fn, var_to_check_against)

                        fn_name = instanced_struct_info.get_mangled_function_name(eq_fn)
                        comparision_code = (
                            f'{fn_name}(&{var_to_check_against}, "{var_to_check}")'
                        )
                        # This entire if & return is redundant, as it is peformed below anyways,
                        # but if we do it here, we don't emit an extra ().
                        if negation_boolean_expression:
                            comparision_code = f"!{comparision_code}"
                        return comparision_code

                    elif lhs["type"] == "char":
                        comparision_code = f"{var_to_check_against} {operators_as_str} '{var_to_check}'"
                    else:
                        # if Char == "\""
                        if var_to_check == '"':
                            comparision_code = f'{var_to_check_against} {operators_as_str} "\\{var_to_check}"'
                        else:
                            comparision_code = (
                                f'{var_to_check_against} == "{var_to_check}"'
                            )

                    if negation_boolean_expression:
                        comparision_code = f"!({comparision_code})"

                    return comparision_code
            elif operators_as_str == "in":
                # if var_to_check in var_to_check_against {
                var_to_check = lhs["variable_name"]
                var_to_check_against = rhs["variable_name"]

                if rhs["type"] == "struct":
                    instanced_struct_info = get_instanced_struct(var_to_check_against)
                    # ^^^^^^^^^^^^^^^^^^^ This always returns sth. because it is already verified in term() as rhs["type"] == "struct".

                    fn_name = instanced_struct_info.get_mangled_function_name(
                        "__contains__"
                    )

                    is_var_to_check_string_object = False
                    var_to_check_struct_info = get_instanced_struct(var_to_check)
                    if var_to_check_struct_info != None:
                        is_var_to_check_string_object = (
                            var_to_check_struct_info.struct_type == "String"
                        )

                    contains_fn_args = instanced_struct_info.get_fn_arguments(
                        "__contains__"
                    )

                    # Get the data type for the first function argument.
                    param_type = contains_fn_args[0].data_type
                    expects_string_argument = (
                        param_type == "char*" or param_type == "str"
                    )

                    if lhs["type"] == "char":
                        gen_code = ""
                        # if var_to_check in var_to_check_against {
                        #    ^^^^^^^^^^^^ this is a char
                        # but the function signature for "__contains__" expects a char*/str.
                        # So, promote the char variable to a string.
                        if expects_string_argument:
                            char_to_string_promotion_code = ""
                            global temp_char_promoted_to_string_variable_count
                            promoted_char_var_name = f"{var_to_check}_promoted_{temp_char_promoted_to_string_variable_count}"
                            # Create a string from char.
                            promoted_char_var_code = f"char {promoted_char_var_name}[2] = {{ {var_to_check}, '\\0'}};"
                            REGISTER_VARIABLE(f"{promoted_char_var_name}", "str")

                            char_to_string_promotion_code += promoted_char_var_code
                            temp_char_promoted_to_string_variable_count += 1

                            LinesCache.append(f"{char_to_string_promotion_code}\n")

                            var_to_check = promoted_char_var_name

                    if lhs["type"] == "string_literal":
                        gen_code = (
                            f'{fn_name}(&{var_to_check_against}, "{var_to_check}")'
                        )
                    elif expects_string_argument and is_var_to_check_string_object:
                        # For Automatic Conversion for String class.
                        # for token in Character_Tokens:
                        #     ^^^^     ^^^^^^^^^^^^^^^^
                        #         String               Dictionary
                        # The __contains__ for Dictionary expects a string,
                        # And the string class has a c_str method that returns char*,
                        # so implement this for String Class. Could be extended to any classes with c_str() method. Food for thought :)
                        c_str_fn_name = (
                            var_to_check_struct_info.get_mangled_function_name("c_str")
                        )
                        gen_code = f"{fn_name}(&{var_to_check_against}, {c_str_fn_name}(&{var_to_check}))"
                    else:
                        gen_code = f"{fn_name}(&{var_to_check_against}, {var_to_check})"
                    return gen_code
                else:
                    if is_variable_array_type(var_to_check_against):
                        array_type = get_type_of_variable(var_to_check_against)
                        global temp_arr_search_variable_count

                        search_variable = f"{var_to_check_against}__contains__{var_to_check}_{temp_arr_search_variable_count}"

                        LinesCache.append(
                            f"bool {search_variable} = false;\n"
                            f"for (unsigned int i = 0; i < {var_to_check_against}_array_size; i++){{\n"
                            f"  if ({var_to_check_against}[i] == {var_to_check}){{\n"
                            f"      {search_variable} = true;\n"
                            f"      break;\n"
                            f"  }}\n"
                            f" }}\n"
                        )

                        temp_arr_search_variable_count += 1
                        REGISTER_VARIABLE(search_variable, f"bool")
                        gen_code = f"{search_variable}"
                        return gen_code

                    else:
                        raise ValueError(
                            f"Target variable {var_to_check_against} is undefined. \n It is neither an array nor a struct."
                        )

            return (
                lhs["output_code"] + " " + operators_as_str + " " + rhs["output_code"]
            )

        return lhs["output_code"]

    if len(parser.tokens) > 0:
        parsed_member = parser.current_token()

        instanced_struct_info = get_instanced_struct(parsed_member)
        is_instanced_struct_ = instanced_struct_info != None

        if is_instanced_struct_:
            # This is to handle function calls on struct members.
            # tokens.push "A"

            parser.next_token()

            # TOKEN_MAP[
            indexed_member_request = parser.check_token(lexer.Token.LEFT_SQUARE_BRACKET)

            # Store the promotion code for char to strings.
            char_to_string_promotion_code = ""

            fn_name = ""

            if indexed_member_request or parser.check_token(lexer.Token.DOT):
                if indexed_member_request:
                    # This should parse
                    # TOKEN_MAP["="] = 1
                    #                  ^____ Value
                    #           ^^^_________ Key

                    parser.consume_token(lexer.Token.LEFT_SQUARE_BRACKET)
                    parameters = [_read_a_parameter()]
                    parser.consume_token(lexer.Token.RIGHT_SQUARE_BRACKET)
                    parser.consume_token(lexer.Token.EQUALS)

                    parameters.append(_read_a_parameter())

                    # For Dictionary Like Data Items.
                    # TOKEN_MAP["="] = 1
                    fn_name = "__setitem__"
                    fn_name = instanced_struct_info.get_mangled_function_name(fn_name)

                    code = ""
                    if len(parameters) > 0:
                        params = []
                        for parameter in parameters:
                            if parameter.param_type == ParameterType.RAW_STRING:
                                m_param = f"{parameter.param}"
                                if m_param == '"':
                                    m_param = '\\"'
                                params.append(f'"{m_param}"')
                            else:
                                params.append(parameter.param)

                        parameters_str = ",".join(params)
                        code = f"{fn_name}(&{parsed_member}, {parameters_str});\n\n"

                    if char_to_string_promotion_code != "":
                        LinesCache.append(f"{char_to_string_promotion_code}\n")

                    LinesCache.append(f"{code}\n")
                elif parser.check_token(lexer.Token.DOT):
                    var_name = parsed_member
                    parser.next_token()

                    fn_name_tmp = parser.current_token()

                    StructInfo = instanced_struct_info.get_struct_defination()

                    # macro type functinons
                    if StructInfo.has_macro(fn_name_tmp):
                        struct_name = parsed_member
                        parse_macro(parsed_member, "CLASS_MACRO", fn_name_tmp)

                        LinesCache.append(f"//Class Macro.\n")
                        continue

                    parser = Parser.Parser(Line)
                    # ctok.push(10)
                    parser.next_token()

                    parse_result = _parse_function_call(parsed_member, False)
                    fn_name = parse_result["fn_name"]
                    return_type = parse_result["return_type"]

                    if parse_result["has_parameters"]:
                        parameters_str = parse_result["parameters_str"]
                        code = f"{fn_name}(&{var_name}, {parameters_str});"
                    else:
                        if HOOKS_hook_fn_name != "":
                            code = f"{fn_name}_hooked_{hook_fn_name}(&{var_name},{HOOKS_target_fn});"
                            HOOKS_hook_fn_name = ""
                            HOOKS_target_fn = ""
                        else:
                            code = f"{fn_name}(&{var_name});"

                    LinesCache.append(f"{code}\n")
                continue
            elif parser.check_token(lexer.Token.PLUS):
                # str += "World"
                parser.next_token()
                parser.consume_token(lexer.Token.EQUALS)

                is_string_literal = False

                if parser.check_token(lexer.Token.QUOTE):
                    # str += "World"
                    parser.consume_token(lexer.Token.QUOTE)
                    is_string_literal = True
                else:
                    # token += Char
                    is_string_literal = False

                string = parser.current_token()
                parser.next_token()
                # print(f"Obtained String : {string}")

                add_fn = "__add__"
                fns_required_for_addition = [add_fn]

                StructInfo = instanced_struct_info.get_struct_defination()

                if struct_type == "String":
                    for fn in fns_required_for_addition:
                        StructInfo.ensure_has_function(fn, parsed_member)

                # fn_name = instanced_struct_info.get_mangled_function_name(add_fn)
                # fn_name_unmangled = add_fn

                gen_code = ""

                # Emit CPL code to perform addition.
                # This line will be parsed by the compiler in next line.
                # The conversion of String class to char*, char to char*,
                # all these are handled by the compiler during function call parse.
                if is_string_literal:
                    # relative_path.__add__(".c")
                    gen_code = f'{parsed_member}.{add_fn}("{string}")'
                else:
                    # relative_path.__add__(module_name)
                    gen_code = f"{parsed_member}.{add_fn}({string})"

                index_to_insert_at = index
                Lines.insert(index_to_insert_at, gen_code)
                continue
            elif parser.check_token(lexer.Token.EQUALS):
                # str = "Reassign"
                parser.consume_token(lexer.Token.EQUALS)

                parsed_string_variable = False

                if parser.check_token(lexer.Token.QUOTE):
                    # str = "Reassign"
                    parser.consume_token(lexer.Token.QUOTE)
                    parsed_string_variable = False
                else:
                    # token = Char #TODO : Not implemented properly.
                    parsed_string_variable = True

                string = parser.current_token()
                parser.next_token()
                # print(f"Obtained String : {string}")

                reassign_fn = "__reassign__"
                fns_required_for_reassignment = [reassign_fn]

                struct_type = instanced_struct_info.struct_type
                StructInfo = instanced_struct_info.get_struct_defination()

                if struct_type == "String":
                    for fn in fns_required_for_reassignment:
                        StructInfo.ensure_has_function(fn, parsed_member)

                fn_name = instanced_struct_info.get_mangled_function_name(reassign_fn)
                fn_name_unmangled = reassign_fn

                gen_code = ""

                if parsed_string_variable:
                    # token = Char
                    raise Exception(
                        "Reassignment operator is only implemented for string arguments."
                    )
                else:
                    gen_code = f'{fn_name}(&{parsed_member}, "{string}");'

                LinesCache.append(f"{gen_code}\n")
                continue

        elif is_macro_name(parsed_member):
            # add_token_raw "="
            parse_macro(parsed_member, "NORMAL_MACRO")

            # Index is already incremented above so,
            # ##Lines.insert(index + 1, code)
            # Lines.insert(index, code)
            continue
        elif is_global_function(parsed_member):
            m_fn = get_global_function_by_name(parsed_member)
            if m_fn.return_type == "void" and len(m_fn.fn_arguments) == 0:
                LinesCache.append(f"{parsed_member}(); \n")
                continue
            else:
                #TODO ?? 
                raise Exception(f"UserDefined Function : {parsed_member} calling void functions can't take parameters as of now. ")
        elif is_variable_boolean_type(parsed_member):
            # escape_back_slash = False
            parser.next_token()

            parser.consume_token(lexer.Token.EQUALS)

            curr_token = parser.get_token()
            is_true_token = curr_token == lexer.Token.TRUE
            is_false_token = curr_token == lexer.Token.FALSE

            if is_true_token:
                LinesCache.append(f"{parsed_member} = true; \n")
            elif is_false_token:
                LinesCache.append(f"{parsed_member} = false; \n")
            else:
                raise ValueError("Expected a boolean value.")
            continue
    else:
        LinesCache.append("\n")
        continue

    if check_token(lexer.Token.RIGHT_CURLY):

        top_of_stack = nesting_levels[-1]

        if top_of_stack == NestingLevel.FOR_LOOP:
            _ = nesting_levels.pop(-1)
            for_loop_depth -= 1
            decrement_scope()
            LinesCache.append("}\n")
        elif top_of_stack == NestingLevel.IF_STATEMENT:
            _ = nesting_levels.pop(-1)
            decrement_scope()
            LinesCache.append("}\n")

            if len(parser.tokens) >= 2:
                parser.next_token()
                if check_token(lexer.Token.ELSE):
                    parser.next_token()
                    LinesCache.append("else")

                    has_else_if_branch = False

                    if len(parser.tokens) >= 1:
                        # else if branch
                        if check_token(lexer.Token.IF):
                            # Merge the line to next line
                            # so it will be parsed as separate if statement.
                            has_else_if_branch = True
                            new_line = Line.split("else", 1)[1]
                            index_to_insert_at = index
                            Lines.insert(index_to_insert_at, new_line)
                        else:
                            has_else_if_branch = False
                    else:
                        has_else_if_branch = False

                    if not has_else_if_branch:
                        # normal else branch.
                        LinesCache.append("{\n")
                        nesting_levels.append(NestingLevel.ELSE_STATEMENT)
                        increment_scope()
        elif top_of_stack == NestingLevel.ELSE_STATEMENT:
            _ = nesting_levels.pop(-1)
            decrement_scope()
            LinesCache.append("}\n")
        else:
            raise Exception("UnImplemented Right Curly.")
    elif check_token(lexer.Token.HASH):
        # Comments.
        # Just create a split at the first '#'.
        parser.next_token()

        if check_token(lexer.Token.INCLUDE):
            # this has to handle #include<string.h> as well.
            LinesCache.append(Line)
        else:
            parts = Line.split("#", 1)
            line_without_hash = parts[1]
            LinesCache.append(f" //{line_without_hash}")
    elif inside_match_loop:
        if "%}" in Line:
            inside_match_loop = False
            # LinesCache.append("}\n")
        elif match_type == "variable":
            # 1 => printf("One");
            rhs = Line.split("=>")
            match_condition_value = rhs[0].strip()
            statements = rhs[1].strip()

            is_else_condition = False
            if match_condition_count == 0:
                LinesCache.append("if ")
            else:
                if match_condition_value == "_":
                    is_else_condition = True
                    LinesCache.append("else")
                else:
                    LinesCache.append("else if")

            if is_else_condition:
                LinesCache.append(f"{{\n")
            else:
                # 3 | 4 | 5
                if "|" in match_condition_value:
                    values_to_test = match_condition_value.split("|")

                    statement_list = []
                    for value in values_to_test:
                        statement_list.append(f"{match_variable_name} == {value}")
                    combined_statement = " || ".join(statement_list)
                    LinesCache.append(f" ({combined_statement}){{\n")
                elif "..." in match_condition_value:
                    # 5 ... 10
                    operands = match_condition_value.split("...")
                    start_range = operands[0]
                    end_range = operands[1]
                    LinesCache.append(
                        f" ( {match_variable_name} >= {start_range} && {match_variable_name} <= {end_range} ){{\n"
                    )
                else:
                    LinesCache.append(
                        f" ( {match_variable_name} == {match_condition_value}){{\n"
                    )
            LinesCache.append(f"    {statements} \n}}\n")

            match_condition_count += 1
        elif match_type == "struct":
            # (0, 0) => printf("Origin.");
            rhs = Line.split("=>")
            match_condition = rhs[0].strip()
            statements = rhs[1].strip()

            type, name = match_struct_type_info
            # print(match_struct_type_info)

            match_condition = match_condition.split("(")[1]
            match_condition = match_condition.split(")")[0]
            conds = match_condition.split(",")

            # Get all the members for the given struct type.
            StructInfo = get_struct_defination_of_type(type)
            if StructInfo is None:
                raise ValueError("Struct undefined.")
            members = StructInfo.members

            cond_struct_members_pair = list(zip(conds, members))
            condition_list = []
            assignment_list = []

            # (x,y,z)
            are_all_conditions_assignments = all(
                conds.strip().isnumeric() for conds, memb in cond_struct_members_pair
            )

            for conds, memb in cond_struct_members_pair:
                conds = conds.strip()
                if conds == "_":
                    continue
                elif conds.isnumeric():
                    condition_list.append(f"({name}.{memb} ==  {conds})")
                    # print(f"if({name}.{memb} ==  {conds}){{")
                else:
                    assignment_list.append(f"int {conds} = {name}.{memb};")
                    # print(f"float {conds} = {name}.{memb};")

            # print(" && ".join(condition_list))
            conditions_joined = " && ".join(condition_list)

            # Bug: #(x,y,z) condition works only on the last arm of match.
            # (x,y,z) is final pattern same as using _.
            # so there can be only one such condition ;D
            # if are_all_conditions_assignments:
            #    LinesCache.append("else{")
            if match_condition_count == 0:
                LinesCache.append("if")
            else:
                LinesCache.append("else if")

            if len(condition_list) == 0:
                # (x,y,z), (_,_,_)
                conditions_joined = "true"
            LinesCache.append(f"({conditions_joined})\n{{\n")

            if len(assignment_list) > 0:
                assignment_joined = "\n".join(assignment_list)
                LinesCache.append(f"    {assignment_joined} \n\n")
            LinesCache.append(f"    {statements} \n}}\n")

            match_condition_count += 1
    elif parser.current_token() == "print":
        # print("Hello {meaning}")
        parser.next_token()

        parser.consume_token(lexer.Token.LEFT_ROUND_BRACKET)
        actual_str = parser.extract_string_literal()
        parser.consume_token(lexer.Token.RIGHT_ROUND_BRACKET)

        braces_open = False
        str_text = ""
        extracted_var_name_list = []
        extracted_var_name = ""
        for char in actual_str:
            if char == "{":
                braces_open = True
                extracted_var_name = ""
            elif char == "}":
                braces_open = False
                format_specifier = "d"
                return_type = get_type_of_variable(extracted_var_name)
                if return_type != None:
                    format_specifier = get_format_specifier(return_type)
                str_text += f"%{format_specifier}"
                extracted_var_name_list.append(extracted_var_name)
            elif braces_open:
                extracted_var_name += char
            else:
                str_text += char

        str_to_write = f'printf("{str_text}"'
        if len(extracted_var_name_list) != 0:
            str_to_write += "," + ",".join(extracted_var_name_list)
        str_to_write += ");\n"
        LinesCache.append(str_to_write)
    elif check_token(lexer.Token.MATCH):
        # % match x{
        parser.consume_token(lexer.Token.MATCH)
        match_variable_name = parser.get_token()
        # print(f"Matching variable {match_variable_name}")
        parser.match_token(lexer.Token.LEFT_CURLY)

        inside_match_loop = True
        match_condition_count = 0

        is_Struct = False
        user_struct_type = ""
        type = ""
        name = ""
        # [struct_type,struct_name]
        for s in instanced_struct_names:
            type = s.struct_type
            name = s.struct_name
            if name == match_variable_name:
                is_Struct = True
                user_struct_type = type
                break

        if is_Struct:
            match_struct_type_info = [type, name]
            match_type = "struct"  # variable,struct
        else:
            match_type = "variable"  # variable,struct
    elif check_token(lexer.Token.LET):
        parser.consume_token(lexer.Token.LET)

        array_name = parser.get_token()
        # print(f"Obtained array name = {array_name}")

        # Custom templated array.
        # let arr<T> = [ 1, 2, 3, 4, 5 ];
        #        ^
        if parser.check_token(lexer.Token.SMALLER_THAN):
            parser.consume_token(lexer.Token.SMALLER_THAN)

            type_name = parser.get_token()
            # print(f"Obtained array type = {type_name}")
            parser.consume_token(lexer.Token.GREATER_THAN)

            parse_let(type_name, array_name)
        elif check_token(lexer.Token.EQUALS):
            # let str = "Hello World";
            # let obj = Point{10, 20};
            #         ^
            parser.consume_token(lexer.Token.EQUALS)

            if parser.check_token(lexer.Token.QUOTE):
                # let str = "Hello World";
                #           ^
                string = parser.extract_string_literal()
                # print(f"Obtained String : {string}")
                LinesCache.append(f'char {array_name}[{len(string)+1}] = "{string}";\n')
                string_variable_names.append(array_name)
            elif parser.check_token(lexer.Token.TRUE) or parser.check_token(
                lexer.Token.FALSE
            ):
                # let inside_string = False
                #                     ^
                REGISTER_VARIABLE(array_name, "bool")
                if parser.check_token(lexer.Token.TRUE):
                    LinesCache.append(f"bool {array_name} = true;\n")
                else:
                    LinesCache.append(f"bool {array_name} = false;\n")
            else:
                # let obj = Point{10, 20};
                #           ^
                #           | struct_type
                struct_name = array_name

                struct_type = parser.get_token()
                # print(f"struct type = {struct_type}")

                StructInfo = get_struct_defination_of_type(struct_type)
                if StructInfo != None:
                    # We are creating a struct type.
                    parse_create_struct(struct_type, struct_name)
                else:
                    # let str3 = str2.strip()
                    var_name = struct_name
                    target = struct_type

                    is_constexpr_dict = is_constexpr_dictionary(target)

                    #  let color = Color["Red"]
                    if is_constexpr_dict:
                        #  let color = Color["Red"]
                        #                   ^
                        actual_value = parse_constexpr_dictionary(target)
                        LinesCache.append(f"int {var_name} = {actual_value};\n")
                    else:
                        parse_access_struct_member(var_name, target)
    elif check_token(lexer.Token.IF):
        nesting_levels.append(NestingLevel.IF_STATEMENT)
        increment_scope()

        parser.consume_token(lexer.Token.IF)

        code = boolean_expression()

        LinesCache.append(f"\nif({code}){{\n")
    elif check_token(lexer.Token.FOR):
        # Normal, Un-Enumerated loops.
        # for current_array_value_variable in array_name{

        # Enumerated loops.
        # for ranged_index_item_variable,current_array_value_variable in enumerate array_name{
        nesting_levels.append(NestingLevel.FOR_LOOP)
        for_loop_depth += 1
        increment_scope()

        parser.consume_token(lexer.Token.FOR)

        current_array_value_variable = parser.get_token()
        ranged_index_item_variable = None

        is_enumerated_for_loop = False

        # Enumerated loop Access probably.
        if parser.check_token(lexer.Token.COMMA):
            parser.next_token()

            is_enumerated_for_loop = True

            ranged_index_item_variable = current_array_value_variable
            # In enumerated loop, ranged_index_item_variable appears first,
            # but we parse 'current_array_value_variable' earlier,
            # so we need to fix their order.

            current_array_value_variable = parser.get_token()

        parser.consume_token(lexer.Token.IN)

        if is_enumerated_for_loop:
            parser.consume_token(lexer.Token.ENUMERATE)

        array_name = parser.get_token()
        parser.match_token(lexer.Token.LEFT_CURLY)

        if is_enumerated_for_loop:
            create_array_enumerator(
                array_name, ranged_index_item_variable, current_array_value_variable
            )
        else:
            if array_name in string_variable_names:
                create_string_iterator(array_name)
            elif is_instanced_struct(array_name):
                create_array_iterator_from_struct(
                    array_name, current_array_value_variable
                )
            else:
                create_normal_array_iterator(array_name, current_array_value_variable)
    elif check_token(lexer.Token.STRUCT):
        # % struct Point {T x, T y };
        parser.consume_token(lexer.Token.STRUCT)
        struct_name = parser.get_token()

        is_struct_templated = False

        generic_data_types = []

        if parser.check_token(lexer.Token.SMALLER_THAN):
            parser.next_token()
            is_struct_templated = True
            # print("Generic Struct")
            # struct GenericStruct<X>{X a, float b};
            #                      ^
            generic_data_type = parser.current_token()
            generic_data_types.append(generic_data_type)
            # print(f"Generic type : {generic_data_type}")
            parser.next_token()
            parser.consume_token(lexer.Token.GREATER_THAN)

        # print(f"Found Struct Defination : {struct_name}")

        parser.consume_token(lexer.Token.LEFT_CURLY)

        struct_members_list = []

        while parser.current_token() != lexer.Token.RIGHT_CURLY:
            struct_member_type = parser.get_token()
            pointerless_struct_member_type = struct_member_type
            if parser.check_token(lexer.Token.ASTERISK):
                struct_member_type += "*"
                parser.next_token()
            struct_member = parser.current_token()

            is_generic = False
            if pointerless_struct_member_type in generic_data_types:
                is_generic = True
                # print(f"{pointerless_struct_member_type} is generic.")

            struct_members_list.append(
                MemberDataType(struct_member_type, struct_member, is_generic)
            )
            # print(
            # f"Type : {struct_member_type} Generic : {is_generic} Struct Member : {struct_member} "
            # )

            parser.next_token()
            if parser.check_token(lexer.Token.COMMA):
                parser.next_token()

        parser.next_token()
        parser.match_token(lexer.Token.SEMICOLON)

        struct_data = Struct(struct_name, struct_members_list)

        if is_struct_templated:
            struct_data.is_class_templated = True
            struct_data.template_defination_variable = generic_data_types[0]
        else:
            # Dont immediately instantiate structs which are generic.
            struct_code = struct_data.get_struct_initialization_code()
            GlobalStructInitCode += struct_code

        struct_definations.append(struct_data)
        # Non generic structs shouldn't be written out early, but since the impl blocks write out functions despite being templated we leave the base templated struct defined, so that the funtions generated don't have defination error.
        # GlobalStructInitCode += struct_code

        # LinesCache.append(code)
        # [struct_name,x,y,z..]
    elif check_token(lexer.Token.IMPL):
        # impl Point say Param1 Param2 ... ParamN
        # void say(struct Point *this) { printf("x : %d , y : %d \n", this->x, this->y); }

        # Skip "impl", its not a keyword, just a placeholder to identify a function call.
        parser.consume_token(lexer.Token.IMPL)

        struct_name = parser.get_token()

        should_write_fn_body = True

        StructInfo = get_struct_defination_of_type(struct_name)
        if StructInfo == None:
            raise ValueError(f'Struct name : "{struct_name}" is undefined.')

        struct_members_list = StructInfo.members
        # print(StructInfo.members)  # [['X', 'a', True], ['float', 'b', False]]

        is_struct_templated = StructInfo.is_templated()

        is_overloaded_fn = False
        overload_for_type = ""

        # impl Vector<String> __contains__ value : T -> bool
        #            ^^^^^^^^ This is custom overload for __contains__ for the template type 'String'.
        if parser.current_token() == lexer.Token.SMALLER_THAN:
            parser.next_token()

            # impl Vector< > __contains__ value : T -> bool
            #             ^ indicates an overloaded function(the base overload)
            curr_token = parser.current_token()
            custom_template_type = "#BASE#"
            if curr_token == lexer.Token.GREATER_THAN:
                pass
            else:
                custom_template_type = parser.get_token()
            # print(f"Custom template for class {custom_template_type}")
            overload_for_type = custom_template_type
            is_overloaded_fn = True

            parser.consume_token(lexer.Token.GREATER_THAN)

        fn_name = parser.current_token()

        # print(f"struct_name : {struct_name}, fn_name : {fn_name}")

        parser.next_token()

        return_type = "void"
        # if fn_name == "__contains__":
        #    return_type = "bool"

        # Other tokens are parameters.

        # impl String __init__ text : str, capacity : int

        parameters = []
        parameters_combined_list = []

        while parser.has_tokens_remaining():
            # impl Vector __contains__ value -> bool
            if parser.check_token(lexer.Token.MINUS):
                parser.next_token()
                if parser.check_token(lexer.Token.GREATER_THAN):
                    parser.next_token()
                    return_type = parse_data_type()
                    # print(f"Function Return Type is : {return_type}")
                    break

            param_name = parser.current_token()
            parser.next_token()
            parser.next_token()
            param_type = parser.get_token()
            # print(f"Param Name : {param_name}, type : {param_type}")

            # Just str means built in String class, so.
            if param_type == "str":
                param_type = "char*"

            parameters.append(MemberDataType(param_type, param_name, False))
            parameters_combined_list.append(f"{param_type} {param_name}")

        # print(f"{parameters}")

        code = ""

        is_fn_constructor_type = fn_name == "__init__"

        # __init__Vector
        # Primitive Name Mangling.
        if is_fn_constructor_type:
            # fn_name = fn_name + struct_name
            structs_with_constructors[struct_name] = parameters

        unmangled_name = fn_name

        if is_struct_templated:
            should_write_fn_body = False
        else:
            fn_name = get_mangled_fn_name(struct_name, fn_name)

            StructInfo = get_struct_defination_of_type(return_type)
            if StructInfo is not None:
                return_type = f"struct {return_type}"

            if len(parameters) > 0:
                parameters_str = ",".join(parameters_combined_list)
                code = f"{return_type} {fn_name}(struct {struct_name} *this, {parameters_str}) {{\n"
            else:
                code = f"{return_type} {fn_name}(struct {struct_name} *this) {{\n"

        currently_reading_fn_name = unmangled_name
        currently_reading_fn_parent_struct = struct_name
        currently_reading_parameters = parameters
        currently_reading_fn_name_unmangled = unmangled_name
        currently_reading_return_type = return_type

        fn = MemberFunction(unmangled_name, parameters, return_type)
        fn.is_overloaded_function = is_overloaded_fn
        fn.overload_for_template_type = overload_for_type

        add_fn_member_to_struct(struct_name, fn)

        impl_body_start_pos = len(GlobalStructInitCode)

        GlobalStructInitCode += code
        is_inside_struct_impl = True
    elif check_token(lexer.Token.ENDFN):
        if is_inside_struct_impl:
            if should_write_fn_body:
                GlobalStructInitCode += "}\n\n"

            currently_reading_fn_body_dup = currently_reading_fn_body
            # Just comment out @hook_{begin/end} symbols.

            # if "@hook_begin" in currently_reading_fn_body:
            # pass
            hook_start_pos = currently_reading_fn_body.find("@hook_begin")
            hook_end_pos = currently_reading_fn_body.find("@hook_end")

            if hook_start_pos != -1 and hook_end_pos != -1:
                # Hook end_pos first to not messup string index.
                currently_reading_fn_body_dup = insert_string(
                    currently_reading_fn_body_dup, hook_end_pos, "//"
                )

                currently_reading_fn_body_dup = insert_string(
                    currently_reading_fn_body_dup, hook_start_pos, "//"
                )

                # This need modification too.

                global_hook_start_pos = GlobalStructInitCode.find("@hook_begin")
                global_hook_end_pos = GlobalStructInitCode.find("@hook_end")
                if global_hook_start_pos != -1 and global_hook_end_pos != -1:
                    index_to_insert_at = global_hook_end_pos

                    GlobalStructInitCode = insert_string(
                        GlobalStructInitCode, global_hook_end_pos, "//"
                    )

                    GlobalStructInitCode = insert_string(
                        GlobalStructInitCode, global_hook_start_pos, "//"
                    )

                hook_body = currently_reading_fn_body[hook_start_pos:hook_end_pos]

                # Get the actual fn_hook_body_without @hook_{begin/end}.
                hook_body_actual = hook_body
                hook_body_actual_start_pos = hook_body_actual.find(")")
                hook_body_actual_end_pos = hook_body_actual.find("@hook_end")
                hook_body_actual = hook_body_actual[
                    hook_body_actual_start_pos:hook_body_actual_end_pos
                ]

                # @hook_begin("custom_integer_printer" "int" data)
                parsed_hook_info = parse_hook_info(hook_body)
                hook_fn_name = parsed_hook_info.hook_fn_name
                hook_fn_arg_type = parsed_hook_info.hook_fn_arg_type
                hook_var_name = parsed_hook_info.hook_var_name

                hooker_fn_code = (
                    f"typedef void (*{hook_fn_name})({hook_fn_arg_type}); \n"
                )

                index_to_insert_at = impl_body_start_pos

                """                        
                index_to_insert_at = impl_body_start_pos
                GlobalStructInitCode = (
                    GlobalStructInitCode[:index_to_insert_at]
                    + hooker_fn_code
                    + GlobalStructInitCode[index_to_insert_at:]
                )
                """

                previous_fn_body = GlobalStructInitCode[index_to_insert_at:]

                # Find the ending pos of fn_name of previous fn_body.
                fn_name_end_pos = previous_fn_body.find("(")
                # Append '_hooked' to the end of fn name.
                previous_fn_body = insert_string(
                    previous_fn_body, fn_name_end_pos, f"_hooked_{hook_fn_name}"
                )

                # Find the ending pos of fn parameter of previous fn_body.
                fn_params_end_pos = previous_fn_body.find(")")
                # Silently pass the hooked_fn as a parameter.
                hooked_fn_as_fn_parameter = f"{hook_fn_name} p_{hook_fn_name}"

                previous_fn_body = insert_string(
                    previous_fn_body,
                    fn_params_end_pos,
                    f", {hooked_fn_as_fn_parameter}",
                )

                hook_body_start_pos = previous_fn_body.find("@hook_begin")
                # We need to call the function pointer at this position.

                # Replace everything between @hook_begin/end.
                pattern = r"@hook_begin.*?@hook_end"
                previous_fn_body = re.sub(
                    pattern, "", previous_fn_body, flags=re.DOTALL
                )

                call_hooked_fn = f"p_{hook_fn_name}({hook_var_name});\n"
                # We somehow inhert //
                # so we place a \n before 'call_hooked_fn'.
                previous_fn_body = insert_string(
                    previous_fn_body, hook_body_start_pos, f"\n{call_hooked_fn}"
                )

                GlobalStructInitCode += "\n" + hooker_fn_code
                GlobalStructInitCode += previous_fn_body

                """

                parameters_duplicated: list = currently_reading_parameters
                parameters_duplicated.append(f"{hook_fn_name} p_{hook_fn_name}")

                # Create a duplicated function.
                add_fn_member_to_struct(
                    currently_reading_fn_parent_struct,
                    MemberFunction(
                        currently_reading_fn_name_unmangled,
                        parameters_duplicated,
                        currently_reading_return_type,
                    ),
                )

                add_fnbody_to_member_to_struct(
                    currently_reading_fn_parent_struct,
                    currently_reading_fn_name + f"_hooked{hook_fn_name}",
                    hooker_fn_code + currently_reading_fn_body,
                )

                """

            add_fnbody_to_member_to_struct(
                currently_reading_fn_parent_struct,
                currently_reading_fn_name,
                currently_reading_fn_body_dup,
            )
            currently_reading_fn_body = ""
            should_write_fn_body = True
            is_inside_struct_impl = False
        else:
            raise Exception("End impl without being in impl block.")
    elif check_token(lexer.Token.DEF):
        # This is used to define function like macros.
        # This macros are just replaced in place.
        """
        def add_token_raw(p_token):
            tokens.push p_token
        enddef

        add_token_raw "="
        """
        parser.consume_token(lexer.Token.DEF)

        fn_name = parser.get_token()

        parser.consume_token(lexer.Token.LEFT_ROUND_BRACKET)

        param1 = ""
        currently_reading_def_paramter_initializer_list = False

        if parser.current_token() != lexer.Token.RIGHT_ROUND_BRACKET:
            # This macro defination has a parameter.
            param1 = parser.current_token()
            # print(f"Obtained First param : {param1}")

            parser.next_token()

            # X...
            if parser.current_token() == lexer.Token.DOT:
                parser.next_token()
                if parser.current_token() == lexer.Token.DOT:
                    parser.next_token()
                    if parser.current_token() == lexer.Token.DOT:
                        parser.next_token()
                        currently_reading_def_paramter_initializer_list = True
                    else:
                        raise Exception("Expected 3 dots for initializer list.")
                else:
                    raise Exception("Expected 3 dots for initializer list.")

        parser.consume_token(lexer.Token.RIGHT_ROUND_BRACKET)

        target_class = ""

        # def pushn(X...) for Vector: <- class macros.
        # def push(X...): <- normal macros
        macro_type = "NORMAL_MACRO"
        if parser.current_token() == lexer.Token.COLON:
            parser.match_token(lexer.Token.COLON)
            macro_type = "NORMAL_MACRO"
        elif parser.current_token() == lexer.Token.FOR:
            parser.consume_token(lexer.Token.FOR)
            macro_type = "CLASS_MACRO"

            target_class = parser.get_token()
            parser.consume_token(lexer.Token.COLON)
            # print(f"The macro is defined for {target_class} class.")

        is_inside_def = True
        currently_reading_def_name = fn_name
        currently_reading_def_parameter = param1
        currently_reading_def_macro_type = macro_type
        currently_reading_def_target_class = target_class
    elif check_token(lexer.Token.CONSTEXPR):
        # constexpr Color = {"Red":255, "Green":200}
        parser.consume_token(lexer.Token.CONSTEXPR)

        dict_name = parser.get_token()
        # print(f"Obtained Constexpr Dictionary name = {dict_name}")

        parser.consume_token(lexer.Token.EQUALS)

        parser.consume_token(lexer.Token.LEFT_CURLY)

        dict = {}

        while True:
            key = parser.extract_string_literal()

            parser.consume_token(lexer.Token.COLON)

            value = parser.get_token()

            dict[key] = value

            if parser.check_token(lexer.Token.RIGHT_CURLY):
                parser.next_token()
                break
            elif parser.check_token(lexer.Token.COMMA):
                parser.consume_token(lexer.Token.COMMA)
            else:
                raise ValueError("Incorrect Constexpr Dictionary Format.")

        if is_constexpr_dictionary(dict_name):
            raise NameError(f'Constexpr dictionary "{dict_name}" already defined.')
        m_dict = ConstexprDictionaryType(dict_name, dict)
        constexpr_dictionaries.append(m_dict)
    elif check_token(lexer.Token.AT):
        # @apply_hook("custom_integer_printer", CustomPrint)
        parser.consume_token(lexer.Token.AT)
        parser.consume_token(lexer.Token.APPLY_HOOK)
        parser.consume_token(lexer.Token.LEFT_ROUND_BRACKET)
        HOOKS_hook_fn_name = parser.extract_string_literal()
        parser.consume_token(lexer.Token.COMMA)
        HOOKS_target_fn = parser.get_token()
        parser.consume_token(lexer.Token.RIGHT_ROUND_BRACKET)
    elif check_token(lexer.Token.FUNCTION):
        # function say()
        # function say(Param1 : type1, Param2 : type2 ... ParamN : typeN)
        # function say(Param1 : type1, Param2 : type2 ... ParamN : typeN) -> return_type
        parser.consume_token(lexer.Token.FUNCTION)
        function_name = parser.get_token()
        parser.consume_token(lexer.Token.LEFT_ROUND_BRACKET)

        return_type = "void"

        parameters = []
        parameters_combined_list = []

        while parser.has_tokens_remaining():
            # function say(Param1 : type1, Param2 : type2 ... ParamN : typeN)
            #                                                               ^
            if parser.check_token(lexer.Token.RIGHT_ROUND_BRACKET):
                # function say()
                #              ^
                break

            # function say(Param1 : type1, Param2 : type2 ... ParamN : typeN)
            #              ^
            param_name = parser.get_token()

            # function say(Param1 : type1, Param2 : type2 ... ParamN : typeN)
            #                     ^
            parser.consume_token(lexer.Token.COLON)

            # function say(Param1 : type1, Param2 : type2 ... ParamN : typeN)
            #                       ^
            param_type = parse_data_type()
            # print(f"Function Param Name : {param_name}, type : {param_type}")

            if parser.has_tokens_remaining():
                # function say(Param1 : type1, Param2 : type2 ... ParamN : typeN)
                #                            ^
                # function say(Param1 : type1)
                #                            ^
                # If not reached the closing ), then expect a comma, to read another parameter.

                if not parser.check_token(lexer.Token.RIGHT_ROUND_BRACKET):
                    parser.consume_token(lexer.Token.COMMA)

            parameters.append(MemberDataType(param_type, param_name, False))
            parameters_combined_list.append(f"{param_type} {param_name}")

            increment_scope()

            p_type = param_type
            if "struct" in param_type:
                p_type = p_type.split("struct")[1]
                p_type = p_type.strip()

            # FIXME : Even variables which aren't structs are registered.
            instance = StructInstance(
                p_type, param_name, False, "", get_current_scope()
            )
            # Function parameters shouldn't be freed at the end of the scope.
            # So, add a tag.
            instance.should_be_freed = False

            instanced_struct_names.append(instance)
            REGISTER_VARIABLE(param_name, p_type)

        parser.consume_token(lexer.Token.RIGHT_ROUND_BRACKET)

        # function say(Param1 : type1, Param2 : type2 ... ParamN : typeN) -> return_type
        if parser.has_tokens_remaining():
            if parser.check_token(lexer.Token.MINUS):
                parser.consume_token(lexer.Token.MINUS)
                parser.consume_token(lexer.Token.GREATER_THAN)
                return_type = parse_data_type()

        # print(f"Function return type : {return_type}")

        code = ""
        if len(parameters) > 0:
            parameters_str = ",".join(parameters_combined_list)
            code = f"{return_type} {function_name}({parameters_str}) {{\n"
        else:
            code = f"{return_type} {function_name}() {{\n"

        m_fn = MemberFunction(function_name, parameters, return_type)
        GlobalFunctions.append(m_fn)

        LinesCache.append(code)

    elif check_token(lexer.Token.ENDFUNCTION):
        # decrement_scope()
        # ^^^^^^^^^^^^^^^^ This calls destructors.
        # Since, we have return statement, that handles the destructors.
        # TODO : What for void functions??
        # They don't have return statements but their destructors should be called.
        # As of now the destructors aren't called.
        code = "}\n"
        LinesCache.append(code)
    elif parser.current_token() == lexer.Token.RETURN:
        parser.consume_token(lexer.Token.RETURN)

        return_name = parser.get_token()

        # return s
        #        ^  s shouldn't be freed, because it is returned.
        # Should be the job of the caller to free it.
        i = 0
        for struct in instanced_struct_names:
            if (
                struct.struct_name == return_name
                and struct.scope == get_current_scope()
            ):
                instanced_struct_names[i].should_be_freed = False
                break
            i += 1

        # Write destructors.
        decrement_scope()
        # Write return itself.
        # We assume we have single return statement.
        LinesCache.append(f"return {return_name};\n")
    else:
        LinesCache.append(Line)

for i in range(len(LinesCache)):
    if "// STRUCT_DEFINATIONS //" in LinesCache[i]:
        LinesCache[i] = GlobalStructInitCode
    elif "// DESTRUCTOR_CODE //" in LinesCache[i]:
        LinesCache[i] = symbol_table.destructor_code_for_all_remaining_variables()

outputFile = open(output_file_name, "w")
for Line in LinesCache:
    outputFile.writelines(Line)
outputFile.close()

import subprocess

try:
    subprocess.run(["clang-format", "-i", output_file_name], check=True)
    print(f"Successfully compiled {output_file_name} & formatted using clang-format.")
except subprocess.CalledProcessError as e:
    print(f"Error running clang-format: {e}")
