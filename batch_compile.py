file_names = [
    "examples\\01_variables.c",
    "examples\\01_variables_GUI_Input.c",
    "examples\\02_List.c",
    "examples\\03_Dictionary.c",
    "examples\\04_Classes.c",
    "examples\\04_b_Classes.c",
    "examples\\05_Strings.c",
    "examples\\06_Vector.c",
    "examples\\07_Set.c",
    "examples\\08_Optional.c",
    "examples\\unique_ptr_source.c",
    "examples\\initializer_list.c",
    "examples\\Reflection.c",
    "examples\\Annotations.c",
    "examples\\constexpr_dict.c",
    "examples\\decorators_inside_fn_body.c",
    "examples\\enumerate_source.c",
    "examples\\fileexample.c",
    "examples\\Files.c",
    "examples\\function_example.c",
    "examples\\expression_parse_test.c",
    "examples\\WebServer.c",
    "Bootstrap\\lexer_test.c",
    "Bootstrap\\Parser.c",
    "Bootstrap\\preprocess_test.c",
]

import subprocess

for file_name in file_names:
    subprocess.run(["python", "preprocess_2.py", "--filename", file_name])

# for file_name in file_names:
#     output_file = file_name.split('.')[0]  
#     subprocess.run(["gcc", "-Wall", "-Wextra", "-o", output_file, output_file + "_generated.c"])