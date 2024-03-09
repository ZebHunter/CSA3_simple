from __future__ import annotations

import re
import sys

from machine.isa import Opcode, Word, write_code

USER_REGISTERS = ["r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11", "r12", "r14", "r15"]


class ArgumentError(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


def is_register(arg: str) -> bool:
    return arg in USER_REGISTERS


def remove_extra_spaces(line: str) -> str:
    line = line.replace("' '", str(ord(" ")))
    return re.sub(r"\s+", " ", line)


def remove_commas(line: str) -> str:
    line = line.replace("','", str(ord(",")))
    return line.replace(",", " ")


def preprocessing(asm_text: str) -> str:
    lines: list[str] = asm_text.splitlines()
    strip_lines = map(str.strip, lines)
    remove_empty_lines = filter(bool, strip_lines)
    removed_commas = map(remove_commas, remove_empty_lines)
    remove_spaces = map(remove_extra_spaces, removed_commas)
    joined: str = "\n".join(remove_spaces)
    return joined


def transform_data(data: str) -> (list[Word], dict[str, int]):
    program_data: list[Word] = []
    variables: dict[str, int] = {}
    address_counter = 2

    for line in data.split("\n"):
        line = line.strip()
        var_description = line.split(":")
        assert len(var_description) == 2 or len(var_description) == 4, "Incorrect assignment"
        name, value = var_description[0], var_description[1].strip()
        assert name[0][-1] != " ", 'Spaces after variable name are not allowed, add ":"'
        assert name not in variables, "Variable already defined."

        if value[0] == '"':
            ascii_values = [ord(char) for char in value[1:-1].replace(r"\n", "\n")]
            ascii_values.insert(0, int(len(ascii_values)))
            variables[name] = address_counter
            for char in ascii_values:
                word = Word(int(address_counter), Opcode.NOP, int(char), 0)
                program_data.append(word)
                address_counter += 1
        else:
            variables[name] = address_counter
            word = Word(int(address_counter), Opcode.NOP, int(value), 0)
            program_data.append(word)
            address_counter += 1

    return program_data, variables


def handle_halt_nop(address_counter, cur_opcode) -> Word:
    return Word(address_counter, cur_opcode, 0, 0)


def handle_branch_instructions(address_counter, cur_opcode, command_arguments, labels) -> Word:
    assert len(command_arguments) == 1, "branch instruction should have 1 argument - label"
    assert labels[command_arguments[0][1:]] is not None, "No such label"
    return Word(address_counter, cur_opcode, labels[command_arguments[0][1:]], 0)


def handle_inc_dec_instructions(address_counter, cur_opcode, command_arguments) -> Word:
    assert len(command_arguments) == 1, "INC/DEC/NEG must have only one argument - register"
    assert is_register(command_arguments[0]), "INC/DEC/NEG first argument should be register"
    return Word(address_counter, cur_opcode, command_arguments[0], 0)


def handle_arithmetic_instructions(address_counter, cur_opcode, command_arguments) -> Word:
    if cur_opcode == Opcode.INC or cur_opcode == Opcode.DEC or cur_opcode == Opcode.NEG:
        return handle_inc_dec_instructions(address_counter, cur_opcode, command_arguments)
    assert len(command_arguments) == 2, "Arithmetic commands should have two registers as args"
    assert is_register(command_arguments[0]), "Arithmetic commands args is registers"
    return Word(address_counter, cur_opcode, command_arguments[0], command_arguments[1])


def handle_ld_instruction(address_counter, cur_opcode, command_arguments) -> Word:
    assert len(command_arguments) == 2, "LD must have 2 arguments"
    assert is_register(command_arguments[0]), "Not registers in arguments"
    if is_register(command_arguments[1]):
        return Word(address_counter, cur_opcode, command_arguments[0], command_arguments[1])
    if command_arguments[1][0] == "[":
        return Word(address_counter, Opcode.LD_ADDR, command_arguments[0], command_arguments[1][1:-1])
    if command_arguments[1].isdigit():
        return Word(address_counter, Opcode.LD_LIT, command_arguments[0], int(command_arguments[1]))
    return Word(address_counter, Opcode.LD_LIT, command_arguments[0], command_arguments[1])


def handle_st_instruction(address_counter, cur_opcode, command_arguments) -> Word:
    assert len(command_arguments) == 2, "ST must have 2 arguments"
    assert is_register(command_arguments[0]), "Not registers in arguments"
    if command_arguments[1][0] == "[":
        return Word(address_counter, Opcode.ST_ADDR, command_arguments[0], command_arguments[1][1:-1])
    return Word(address_counter, cur_opcode, command_arguments[0], command_arguments[1])


def handle_mem_operation(address_counter, cur_opcode, command_arguments) -> Word:
    if cur_opcode == Opcode.LD:
        return handle_ld_instruction(address_counter, cur_opcode, command_arguments)
    return handle_st_instruction(address_counter, cur_opcode, command_arguments)


def parse_label(text: str) -> (int, dict[str, int]):
    address_counter: int = 2
    start_address: int = -1
    labels: dict[str, int] = {}
    for instr in text.split("\n"):
        decoding = instr.split(" ")
        if decoding[0][0] == ".":
            current_label = decoding[0]
            assert len(decoding) == 1, "Error parsing label."
            assert current_label[-1] == ":", 'Label format error, excepted ":" mark'
            assert current_label.find(":"), 'Label format error, excepted ":" mark'
            current_label = decoding[0][1:-1]
            if current_label == "start":
                start_address = address_counter
            labels[current_label] = address_counter
        else:
            address_counter += 1
    return start_address, labels


def process_instructions(
        text: str
) -> (int, list[Word]):
    assert text.find(".start:") != -1, ".start label not found"
    start_address, labels = parse_label(text)
    address_counter: int = 2
    command_mem: list[Word] = []

    for instr in text.split("\n"):
        decoding = instr.split(" ")
        if decoding[0][0] != ".":
            cur_opcode = Opcode(decoding[0].upper())
            current_instruction = None
            command_arguments = decoding[1:]

            if cur_opcode in [Opcode.HALT, Opcode.NOP]:
                current_instruction = handle_halt_nop(address_counter, cur_opcode)

            elif cur_opcode in [Opcode.JE, Opcode.JUMP, Opcode.JG,
                                Opcode.JLE, Opcode.JL, Opcode.JNE,
                                Opcode.JG, Opcode.JGE]:
                current_instruction = handle_branch_instructions(address_counter, cur_opcode, command_arguments, labels)

            elif cur_opcode in [Opcode.ADD, Opcode.MOD, Opcode.DIV,
                                Opcode.SUB, Opcode.CMP, Opcode.AND,
                                Opcode.OR, Opcode.XOR, Opcode.MV,
                                Opcode.INC, Opcode.DEC, Opcode.NEG]:
                current_instruction = handle_arithmetic_instructions(address_counter, cur_opcode, command_arguments)

            elif cur_opcode == Opcode.LD or Opcode.ST:
                current_instruction = handle_mem_operation(address_counter, cur_opcode, command_arguments)

            assert current_instruction is not None, "Instruction parsing error"
            command_mem.append(current_instruction)
            address_counter += 1

    return start_address, command_mem


def compilation(command_mem: list[Word], data_mem: list[Word], variables: dict[str, int]) -> list[Word]:
    for word in data_mem:
        word.index += len(command_mem)
    for word in command_mem:
        if word.opcode == Opcode.LD_ADDR or word.opcode == Opcode.LD_LIT or word.opcode == Opcode.ST_ADDR:
            if word.arg2 in variables:
                word.arg2 = variables[str(word.arg2)] + len(command_mem)
            elif word.arg2 == "INP":
                word.arg2 = 0
            elif word.arg2 == "OUT":
                word.arg2 = 1
    return command_mem + data_mem


def transform_to_instruction(code: dict) -> list[Word]:
    memory: list[Word] = []
    for key in code.keys():
        if key == "code_mem":
            for item in code[key]:
                memory.append(item)
        elif key == "data_mem":
            count = len(code["code_mem"])
            for item in code[key]:
                item.set_word(item.index + count)
                memory.append(item)
    return memory


def add_ports(mem: list[Word]) -> None:
    mem.insert(0, Word(0, Opcode.NOP, 0, 0))
    mem.insert(1, Word(1, Opcode.NOP, 0, 0))


def translation(source: str, target: str) -> None:
    code = preprocessing(source)
    data_mem: list[Word] = []
    variables = {}

    text_index = code.find("section .text")

    assert text_index != -1, "No .text section"
    text_start, text_stop = text_index + len("section .text") + 1, None
    data_index = code.find("section .data")
    if data_index == -1:
        text_stop = len(code)
    else:
        data_start, data_stop = data_index + len("section .data") + 1, None
        if data_index < text_index:
            data_stop = text_index - 1
            text_stop = len(code)
        else:
            text_stop = data_index - 1
            data_stop = len(code)
        data_mem, variables = transform_data(code[data_start:data_stop])

    start, command_mem = process_instructions(code[text_start:text_stop])
    memory = compilation(command_mem, data_mem, variables)
    add_ports(memory)
    write_code(target, memory)


def main(args):
    assert len(args) == 2, "Usage: translation.py <source> <output>"
    source = args[0]
    target = args[1]
    with open(source, encoding="utf-8") as file:
        code = file.read()
    translation(code, target)


if __name__ == "__main__":
    sys.path.append("")
    main(sys.argv[1:])
